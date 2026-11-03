"""Synchronous media HTTP methods backed exclusively by the network transport."""

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import suppress
from typing import Any
from uuid import uuid4

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPBearer
from starlette.datastructures import Headers, UploadFile
from starlette.formparsers import MultiPartException

from inference_gateway.adapters.batch_store import BatchConflict, BatchStore, request_fingerprint
from inference_gateway.adapters.runner_contract_v2 import MEDIA_PROFILES
from inference_gateway.core.backend import BackendError
from inference_gateway.core.native import NativeRequest, NativeStream
from inference_gateway.native import response_headers

SYNCHRONOUS_PROFILES = {name: value for name, value in MEDIA_PROFILES.items() if name != "videos"}


def router(
    store: BatchStore,
    open_network: Callable[[NativeRequest], Awaitable[NativeStream]],
    owner_for: Callable[[Request], Awaitable[str]],
    *,
    timeout: float,
    max_input: int = 32 * 1024 * 1024,
    max_output: int = 128 * 1024 * 1024,
    open_for_request: Callable[[Request, NativeRequest], Awaitable[NativeStream]] | None = None,
) -> APIRouter:
    api = APIRouter(dependencies=[Depends(HTTPBearer(auto_error=False))])

    async def parse(request: Request) -> tuple[str, bytes, bytes]:
        content_type = request.headers.get("content-type", "application/json")
        if len(request.headers.getlist("content-type")) > 1 or any(
            char in content_type for char in "\r\n"
        ):
            raise HTTPException(400, "Ambiguous content type")
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type not in {"application/json", "multipart/form-data"}:
            raise HTTPException(415, "Unsupported batch content type")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > max_input:
                raise HTTPException(413, "Batch request exceeds the input limit")
        raw = bytes(body)
        if request.headers.get("content-encoding", "identity") != "identity" or request.url.query:
            raise HTTPException(400, "Encoded bodies and query extensions are unsupported")
        if media_type == "multipart/form-data":
            from starlette.formparsers import MultiPartParser

            async def chunks() -> AsyncGenerator[bytes, None]:
                yield raw

            form = await MultiPartParser(
                Headers(request.headers),
                chunks(),
                max_files=4,
                max_fields=32,
                max_part_size=max_input,
            ).parse()
            fields: dict[str, Any] = {}
            try:
                for key, value in form.multi_items():
                    if key in fields:
                        raise HTTPException(400, "Duplicate multipart fields are unsupported")
                    if isinstance(value, UploadFile):
                        fields[key] = {
                            "file_sha256": hashlib.sha256(await value.read()).hexdigest(),
                            "content_type": value.content_type,
                        }
                    else:
                        fields[key] = str(value)
            finally:
                await form.close()
        else:

            def pairs(entries: list[tuple[str, Any]]) -> dict[str, Any]:
                result: dict[str, Any] = {}
                for key, value in entries:
                    if key in result:
                        raise ValueError()
                    result[key] = value
                return result

            fields = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
        model = fields.get("model") if isinstance(fields, dict) else None
        if (
            not isinstance(model, str)
            or not model
            or len(model) > 96
            or model.strip() != model
            or not (fields.get("stream", False) is False or fields.get("stream") == "false")
        ):
            raise HTTPException(400, "A model and synchronous request are required")
        canonical = json.dumps(fields, sort_keys=True, allow_nan=False).encode()
        return model, raw, canonical

    def endpoint(operation: str, path: str) -> Callable[[Request], Awaitable[Response]]:
        async def run(request: Request) -> Response:
            identifier = "req_" + uuid4().hex
            row = None
            owns_request = False
            stream = None
            iterator = None
            completed = False
            watcher = None
            started = time.monotonic()
            try:
                owner = await owner_for(request)
                model, raw, canonical = await parse(request)
                fingerprint = request_fingerprint(operation, model, canonical)
                key = request.headers.get("idempotency-key")
                if key is not None and (not key.strip() or len(key) > 256):
                    raise HTTPException(400, "Invalid idempotency key")
                row, fresh = store.begin(owner, model, operation, fingerprint, key)
                owns_request = fresh
                if not fresh:
                    status, headers, body = store.replay(owner, row)
                    response = Response(body, status_code=status, headers=dict(headers))
                    response.headers.update(
                        {
                            "x-request-id": identifier,
                            "request-id": identifier,
                            "x-batch-id": row["id"],
                            "x-artifact-id": row["response_artifact"],
                            "cache-control": "private, no-store",
                        }
                    )
                    return response
                task = asyncio.current_task()

                async def disconnected() -> None:
                    while True:
                        if await request.is_disconnected():
                            if task:
                                task.cancel()
                            return
                        await asyncio.sleep(0.1)

                watcher = asyncio.create_task(disconnected())
                safe_headers = tuple(
                    (key, value)
                    for key, value in request.headers.items()
                    if key.lower() in {"content-type", "accept"}
                )
                command = NativeRequest(
                    identifier, model, operation, path, b"", safe_headers, raw, False
                )
                async with asyncio.timeout(timeout):
                    stream = (
                        await open_for_request(request, command)
                        if open_for_request
                        else await open_network(command)
                    )
                    store.bind(row["id"], stream.job_id)
                    if any(
                        k.lower() == "content-encoding" and v not in {"", "identity"}
                        for k, v in stream.headers
                    ):
                        raise BackendError("Encoded batch output is unsupported")
                    iterator = stream.__aiter__()
                    output = bytearray()
                    async for chunk in iterator:
                        output.extend(chunk)
                        if len(output) > max_output:
                            raise BackendError("Batch output exceeds its admitted limit")
                    headers = response_headers(stream.headers)
                    artifact = store.finish(
                        owner,
                        row["id"],
                        stream.status_code,
                        headers,
                        bytes(output),
                        {
                            "output_bytes": len(output),
                            "gateway_wall_seconds": time.monotonic() - started,
                        },
                    )
                    if 200 <= stream.status_code < 300:
                        await stream.complete()
                    completed = True
                    response = Response(
                        bytes(output),
                        status_code=stream.status_code,
                        headers=dict(headers),
                    )
                    response.headers.update(
                        {
                            "x-request-id": identifier,
                            "request-id": identifier,
                            "x-batch-id": row["id"],
                            "x-artifact-id": artifact,
                            "cache-control": "private, no-store",
                        }
                    )
                    return response
            except HTTPException as error:
                status, code = error.status_code, "invalid_request"
            except BatchConflict:
                status, code = 409, "idempotency_conflict"
            except (ValueError, TypeError, MultiPartException):
                status, code = 400, "invalid_request"
            except KeyError:
                status, code = 410, "batch_result_expired"
            except BackendError as error:
                status, code = error.status_code, "network_batch_failed"
            except TimeoutError:
                status, code = 504, "network_batch_timeout"
            except asyncio.CancelledError:
                if row and owns_request:
                    store.fail(row["id"], "upstream_outcome_unknown")
                raise
            except Exception:
                status, code = 502, "network_batch_failed"
            finally:
                if watcher:
                    watcher.cancel()
                    with suppress(asyncio.CancelledError):
                        await watcher
                if stream is not None:
                    with anyio.CancelScope(shield=True):
                        try:
                            if not completed:
                                with suppress(Exception):
                                    await stream.disconnected()
                        finally:
                            try:
                                if iterator is not None:
                                    await iterator.aclose()
                            finally:
                                await stream.aclose()
            if row and owns_request:
                store.fail(row["id"], code)
            return JSONResponse(
                {"error": {"type": "api_error", "code": code, "message": code.replace("_", " ")}},
                status_code=status,
                headers={
                    "x-request-id": identifier,
                    "request-id": identifier,
                    "cache-control": "private, no-store",
                    "x-should-retry": "false",
                },
            )

        return run

    for operation, profile in SYNCHRONOUS_PROFILES.items():
        api.add_api_route(
            profile["path"],
            endpoint(operation, profile["path"]),
            methods=["POST"],
            operation_id=operation,
        )

    @api.get("/v1/artifacts/{artifact_id}")
    async def artifact(artifact_id: str, request: Request) -> Response:
        owner = await owner_for(request)
        try:
            path, media_type = store.artifact(owner, artifact_id)
        except KeyError:
            raise HTTPException(404, "Artifact not found") from None
        return FileResponse(
            path, media_type=media_type, headers={"Cache-Control": "private, no-store"}
        )

    return api
