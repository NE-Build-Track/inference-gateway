"""HTTP adapters for descriptor-backed media operations, jobs and artifacts."""

from __future__ import annotations

import asyncio
import base64
import copy
import json
import time
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any, NoReturn
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.routing import APIRoute
from fastapi.security import HTTPBearer
from jsonschema import ValidationError

from inference_gateway.adapters.capability_jobs import JobConflict, Jobs

ROUTES = {
    "images_generations": "/v1/images/generations",
    "images_edits": "/v1/images/edits",
    "audio_speech": "/v1/audio/speech",
    "audio_transcriptions": "/v1/audio/transcriptions",
    "videos": "/v1/videos",
    "image_transformations": "/v1/image-transformations",
    "music_generations": "/v1/music/generations",
    "meshes_generations": "/v1/meshes/generations",
}


class CapabilityRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def invoke(request: Request) -> Response:
            identifier = "req_" + uuid4().hex
            try:
                response = await handler(request)
            except HTTPException as error:
                detail = (
                    error.detail
                    if isinstance(error.detail, dict)
                    else {"message": str(error.detail), "type": "api_error"}
                )
                response = JSONResponse({"error": detail}, status_code=error.status_code)
            response.headers["x-request-id"] = identifier
            response.headers["request-id"] = identifier
            return response

        return invoke


def router(
    jobs: Jobs,
    owner_for: Callable[[Request], Awaitable[str]],
    input_root: str,
    voices: dict[str, dict[str, str]] | None = None,
) -> APIRouter:
    api = APIRouter(
        route_class=CapabilityRoute, dependencies=[Depends(HTTPBearer(auto_error=False))]
    )
    inputs = Path(input_root).resolve()
    inputs.mkdir(parents=True, exist_ok=True)
    jobs.input_root = inputs
    jobs.db.execute(
        "CREATE TABLE IF NOT EXISTS media_uploads "
        "(id TEXT PRIMARY KEY,owner TEXT NOT NULL,path TEXT NOT NULL,expires REAL NOT NULL)"
    )
    for name, voice in (voices or {}).items():
        preset_path = (inputs / voice["audio"]).resolve()
        if (
            not preset_path.is_relative_to(inputs)
            or not preset_path.is_file()
            or not voice.get("text")
        ):
            raise ValueError("Invalid operator voice preset")
        jobs.db.execute(
            "INSERT OR REPLACE INTO media_uploads VALUES (?,?,?,?)",
            ("voice_" + name, "*", str(preset_path.relative_to(inputs)), 0),
        )

    async def owner(request: Request) -> str:
        return await owner_for(request)

    def fail(status: int, code: str) -> NoReturn:
        raise HTTPException(status, {"type": "invalid_request_error", "code": code})

    def staged(user: str, values: dict[str, Any]) -> dict[str, Any]:
        result = dict(values)
        for field in ("image", "audio", "reference_audio"):
            if field not in result:
                continue
            row = jobs.db.execute(
                "SELECT path FROM media_uploads WHERE id=? "
                "AND ((owner=? AND expires>?) OR owner='*')",
                (result[field], user, time.time()),
            ).fetchone()
            if row is None:
                fail(400, "unknown_input_file")
            result[field] = row["path"]
        return result

    async def upload(user: str, file: Any) -> str:
        identifier = "file_" + uuid4().hex
        relative = identifier + Path(file.filename or "input.bin").suffix.lower()
        if len(relative) > 100:
            fail(400, "invalid_filename")
        path = inputs / relative
        total = 0
        try:
            with path.open("xb") as target:
                while chunk := await file.read(65536):
                    total += len(chunk)
                    if total > 25 * 1024 * 1024:
                        fail(413, "input_too_large")
                    target.write(chunk)
            if not total:
                fail(400, "empty_input")
            jobs.db.execute(
                "INSERT INTO media_uploads VALUES (?,?,?,?)",
                (identifier, user, relative, time.time() + jobs.retention),
            )
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return identifier

    async def body(request: Request, user: str) -> dict[str, Any]:
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            form = await request.form(max_files=4, max_fields=32, max_part_size=25 * 1024 * 1024)
            result: dict[str, Any] = {}
            for key, value in form.multi_items():
                if key in result:
                    fail(400, "duplicate_field")
                if hasattr(value, "filename"):
                    result[key] = await upload(user, value)
                else:
                    result[key] = str(value)
            return result
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 1024 * 1024:
                fail(413, "input_too_large")
        try:
            result = json.loads(raw)
            if not isinstance(result, dict):
                fail(400, "invalid_body")
            return result
        except (ValueError, TypeError):
            fail(400, "invalid_json")
        raise AssertionError

    async def submit(
        user: str, capability: str, values: dict[str, Any], request: Request
    ) -> dict[str, Any]:
        if capability not in jobs.catalog:
            fail(400, "unsupported_capability")
        try:
            jobs.catalog[capability].validate(values)
            return await jobs.submit(
                user, capability, staged(user, values), request.headers.get("idempotency-key")
            )
        except (ValidationError, ValueError) as error:
            fail(409 if isinstance(error, JobConflict) else 400, "invalid_capability_input")
        except asyncio.QueueFull:
            fail(503, "capacity_unavailable")
        raise AssertionError

    @api.get("/v1/capabilities")
    async def capabilities(request: Request) -> dict[str, Any]:
        await owner(request)
        return {
            "data": [
                {
                    "id": c.id,
                    "model": c.document["model"],
                    "operation": c.document["operation"],
                    "admission": "candidate",
                    "input_schema": c.document["input_schema"],
                    "tool": c.tool(),
                }
                for c in jobs.catalog.values()
            ]
        }

    @api.post("/v1/files")
    async def files(request: Request) -> dict[str, Any]:
        user = await owner(request)
        values = await body(request, user)
        if set(values) - {"file", "purpose"} or "file" not in values:
            fail(400, "invalid_file_upload")
        return {
            "id": values["file"],
            "object": "file",
            "purpose": values.get("purpose", "assistants"),
        }

    @api.post(
        "/v1/jobs",
        status_code=202,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "oneOf": [
                                {
                                    "type": "object",
                                    "properties": {
                                        "capability": {"const": spec.id, "type": "string"},
                                        "input": spec.document["input_schema"],
                                    },
                                    "required": ["capability", "input"],
                                    "additionalProperties": False,
                                }
                                for spec in jobs.catalog.values()
                            ]
                        }
                    }
                },
            }
        },
    )
    async def create_job(request: Request) -> dict[str, Any]:
        user = await owner(request)
        values = await body(request, user)
        if set(values) != {"capability", "input"} or not isinstance(values["input"], dict):
            fail(400, "invalid_job")
        return await submit(user, values["capability"], values["input"], request)

    @api.get("/v1/jobs/{job_id}")
    async def get_job(job_id: str, request: Request) -> dict[str, Any]:
        try:
            return jobs.get(await owner(request), job_id)
        except KeyError:
            fail(404, "job_not_found")
        raise AssertionError

    @api.post("/v1/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str, request: Request) -> dict[str, Any]:
        try:
            return await jobs.cancel(await owner(request), job_id)
        except KeyError:
            fail(404, "job_not_found")
        raise AssertionError

    @api.get("/v1/artifacts/{artifact_id}")
    async def get_artifact(artifact_id: str, request: Request) -> Response:
        try:
            path, media_type = jobs.artifact(await owner(request), artifact_id)
        except KeyError:
            fail(404, "artifact_not_found")
        return FileResponse(
            path, media_type=media_type, headers={"Cache-Control": "private, no-store"}
        )

    def endpoint(operation: str) -> Callable[[Request], Awaitable[Response]]:
        async def generate(request: Request) -> Response:
            user = await owner(request)
            values = await body(request, user)
            model = values.pop("model", None)
            matches = [
                c
                for c in jobs.catalog.values()
                if c.document["operation"] == operation and c.document["model"]["source"] == model
            ]
            if len(matches) != 1:
                fail(400, "model_not_available")
            spec = matches[0]
            response_format = values.get("response_format", "json")
            try:
                from importlib import import_module

                schema = spec.document.get("wire_schema", spec.document["input_schema"])
                if request.headers.get("content-type", "").startswith("multipart/form-data"):
                    for field, rule in schema["properties"].items():
                        if field in values and rule.get("type") == "integer":
                            values[field] = int(values[field])
                values = import_module("stream_pack.protocols").normalize_wire(
                    spec.document, values
                )
            except (ValidationError, ValueError, TypeError):
                fail(400, "invalid_capability_input")
            if operation == "audio_speech" and "voice" in values:
                voice = values.pop("voice")
                if voices is None or voice not in voices:
                    fail(400, "voice_not_configured")
                values.update(
                    reference_audio="voice_" + voice, reference_text=voices[voice]["text"]
                )
            job = await submit(user, spec.id, values, request)
            if operation in {"videos", "music_generations", "meshes_generations"}:
                return JSONResponse(
                    video(job) if operation == "videos" else job,
                    status_code=200 if operation == "videos" else 202,
                )
            try:
                while job["status"] in {"queued", "running"}:
                    if await request.is_disconnected():
                        await jobs.cancel(user, job["id"])
                        return Response(status_code=499)
                    await asyncio.sleep(0.05)
                    job = jobs.get(user, job["id"])
            except asyncio.CancelledError:
                await jobs.cancel(user, job["id"])
                raise
            if job["status"] != "succeeded":
                fail(502, "generation_failed")
            result = job["result"]
            if operation == "audio_transcriptions":
                if response_format == "text":
                    return Response(result["text"], media_type="text/plain")
                return JSONResponse({"text": result["text"]})
            item = result["artifacts"][0]
            path, media_type = jobs.artifact(user, item["id"])
            if operation == "audio_speech":
                return FileResponse(path, media_type=media_type)
            return JSONResponse(
                {
                    "created": job["created_at"],
                    "data": [{"b64_json": base64.b64encode(path.read_bytes()).decode()}],
                }
            )

        return generate

    def video(job: dict[str, Any]) -> dict[str, Any]:
        states = {"queued": "queued", "running": "in_progress", "succeeded": "completed"}
        return {
            "id": job["id"],
            "object": "video",
            "status": states.get(job["status"], "failed"),
            "created_at": job["created_at"],
        }

    @api.get("/v1/videos/{job_id}")
    async def get_video(job_id: str, request: Request) -> dict[str, Any]:
        job = await get_job(job_id, request)
        if jobs.catalog[job["capability"]].document["operation"] != "videos":
            fail(404, "video_not_found")
        return video(job)

    @api.get("/v1/videos/{job_id}/content")
    async def video_content(job_id: str, request: Request) -> Response:
        job = await get_job(job_id, request)
        if jobs.catalog[job["capability"]].document["operation"] != "videos":
            fail(404, "video_not_found")
        if job["status"] != "succeeded":
            fail(409, "video_not_ready")
        return await get_artifact(job["result"]["artifacts"][0]["id"], request)

    for operation, path in ROUTES.items():
        variants = []
        for spec in jobs.catalog.values():
            if spec.document["operation"] == operation:
                schema = copy.deepcopy(
                    spec.document.get("wire_schema", spec.document["input_schema"])
                )
                schema["properties"]["model"] = {
                    "const": spec.document["model"]["source"],
                    "type": "string",
                }
                schema["required"].append("model")
                variants.append(schema)
        if variants:
            content = {"application/json": {"schema": {"oneOf": variants}}}
            if operation in {"images_edits", "audio_transcriptions", "videos"}:
                multipart = copy.deepcopy(variants)
                for variant in multipart:
                    for field in ("image", "file", "input_reference", "reference_audio"):
                        if field in variant["properties"]:
                            variant["properties"][field] = {"type": "string", "format": "binary"}
                content["multipart/form-data"] = {"schema": {"oneOf": multipart}}
            api.add_api_route(
                path,
                endpoint(operation),
                methods=["POST"],
                operation_id=operation,
                openapi_extra={
                    "requestBody": {
                        "required": True,
                        "content": content,
                    }
                },
            )
    return api
