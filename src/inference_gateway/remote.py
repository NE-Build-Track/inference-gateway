"""Compose the caller-authenticated remote Core media gateway."""

import asyncio
import copy
import hashlib
import json
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from inference_gateway.adapters.batch_store import BatchStore
from inference_gateway.adapters.network_batch import router
from inference_gateway.adapters.runner_contract_v2 import MEDIA_PROFILES
from inference_gateway.backends.remote_railway import RemoteRailwayTransport
from inference_gateway.config import Settings
from inference_gateway.core.backend import BackendUnavailable
from inference_gateway.core.native import NativeRequest, NativeStream


def create_remote_app(config: Settings, client: httpx.AsyncClient | None = None) -> FastAPI:
    assert config.engine_url and config.capability_openapi_file and config.batch_database
    url = urlsplit(config.engine_url)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
    ):
        raise ValueError("Remote Core requires an HTTPS origin without credentials")
    document = json.loads(Path(config.capability_openapi_file).read_text())
    approvals = json.loads(
        Path(config.capability_openapi_file).with_name("native-approvals.json").read_text()
    )
    definitions: dict[tuple[str, str], dict[str, Any]] = {}
    for item in document["paths"].values():
        operation = item["post"]
        binding = operation["x-livepeer"]
        if binding["path"] not in {p["path"] for p in MEDIA_PROFILES.values()}:
            raise ValueError("Remote backend supports admitted media operations only")
        key = (binding["model"], binding["path"])
        if key in definitions:
            raise ValueError("Duplicate remote capability")
        for content in operation["requestBody"]["content"].values():
            Draft202012Validator.check_schema(content["schema"])
        definitions[key] = operation
    store = BatchStore(
        config.batch_database, config.batch_artifact_dir, retention=config.batch_retention_seconds
    )
    http = client or httpx.AsyncClient(
        base_url=config.engine_url,
        timeout=config.timeout_seconds,
        follow_redirects=False,
        trust_env=False,
    )
    transport = RemoteRailwayTransport(http, config.timeout_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async def expire() -> None:
            while True:
                store.expire()
                await asyncio.sleep(min(60, config.batch_retention_seconds))

        maintenance = None
        try:
            with store.server_lock():
                store.recover()
                maintenance = asyncio.create_task(expire())
                yield
        finally:
            if maintenance:
                maintenance.cancel()
                await asyncio.gather(maintenance, return_exceptions=True)
            store.close()
            if client is None:
                await http.aclose()

    app = FastAPI(title="Livepeer remote media gateway", version="0.2.2", lifespan=lifespan)

    async def owner(request: Request) -> str:
        credential = request.headers.get("authorization", "")
        if not credential.startswith("Bearer ") or len(credential) > 16384:
            raise HTTPException(401, "A Railway Builder credential is required")
        try:
            async with http.stream(
                "GET", "/v1/capabilities", headers={"Authorization": credential}
            ) as response:
                if response.status_code in {401, 403}:
                    raise HTTPException(response.status_code, "Core authorization refused")
                if response.status_code != 200:
                    raise HTTPException(503, "Core capability discovery unavailable")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 4 * 1024 * 1024:
                        raise HTTPException(503, "Core catalog exceeds its limit")
            profiles = json.loads(data)
            if not isinstance(profiles, list):
                raise ValueError()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, "Core capability discovery unavailable") from None
        admitted = []
        for profile in profiles:
            operation = definitions.get((profile.get("model"), profile.get("path")))
            if operation is None:
                continue
            binding = operation["x-livepeer"]
            if not any(
                all(approval.get(k) == binding[k] for k in ("app", "model", "deployment_revision"))
                and MEDIA_PROFILES.get(approval.get("endpoint"), {}).get("path") == binding["path"]
                and approval.get("expires_at", 0) > time.time()
                for approval in approvals
            ):
                continue
            content = operation["requestBody"]["content"].get(profile.get("content_type"))
            if (
                content
                and all(
                    profile.get(k) == binding[k]
                    for k in ("app", "model", "path", "deployment_revision")
                )
                and content["schema"] == profile.get("input_schema")
            ):
                admitted.append(profile)
        request.state.remote_profiles = admitted
        # Credential-scoped receipts avoid trusting unsigned JWT claims or sharing artifacts.
        return hashlib.sha256(credential.encode()).hexdigest()

    async def unused(command: NativeRequest) -> NativeStream:
        raise BackendUnavailable()

    async def open_for(request: Request, command: NativeRequest) -> NativeStream:
        profile = next(
            (
                p
                for p in request.state.remote_profiles
                if p["model"] == command.model and p["path"] == command.path
            ),
            None,
        )
        if profile is None:
            raise HTTPException(503, "No matching admitted Core capability")
        operation = definitions[(command.model, command.path)]
        content_type = (
            dict(command.headers).get("content-type", "application/json").split(";", 1)[0]
        )
        schema = operation["requestBody"]["content"].get(content_type)
        if schema is None:
            raise HTTPException(415, "Content type is not admitted for this operation")
        if content_type == "application/json":
            if not Draft202012Validator(schema["schema"]).is_valid(json.loads(command.body)):
                raise HTTPException(400, "Input does not match the capability schema")
        elif content_type == "multipart/form-data":
            from starlette.datastructures import Headers, UploadFile
            from starlette.formparsers import MultiPartParser

            async def chunks() -> AsyncGenerator[bytes, None]:
                yield command.body

            form = await MultiPartParser(Headers(dict(command.headers)), chunks()).parse()
            try:
                values = {
                    k: "uploaded-media" if isinstance(v, UploadFile) else v
                    for k, v in form.multi_items()
                }
                if not Draft202012Validator(schema["schema"]).is_valid(values):
                    raise HTTPException(400, "Input does not match the capability schema")
            finally:
                await form.close()
        return await transport.open(request.headers["authorization"], profile, command)

    api = router(
        store,
        unused,
        owner,
        timeout=config.timeout_seconds,
        max_input=8 * 1024 * 1024,
        open_for_request=open_for,
    )
    paths = {path for _, path in definitions}
    api.routes[:] = [
        route
        for route in api.routes
        if getattr(route, "path", "") in paths
        or getattr(route, "path", "").startswith("/v1/artifacts/")
    ]
    app.include_router(api)

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready() -> JSONResponse:
        try:
            response = await http.get("/readyz")
            ok = response.status_code == 200
        except httpx.HTTPError:
            ok = False
        return JSONResponse(
            {"status": "ready" if ok else "unavailable"}, status_code=200 if ok else 503
        )

    @app.get("/v1/capabilities")
    async def capabilities(request: Request) -> list[dict[str, Any]]:
        await owner(request)
        return list(request.state.remote_profiles)

    @app.get("/v1/models")
    async def models(request: Request) -> dict[str, Any]:
        await owner(request)
        return {
            "object": "list",
            "data": [
                {"id": name, "object": "model", "created": 0, "owned_by": "livepeer"}
                for name in sorted({p["model"] for p in request.state.remote_profiles})
            ],
        }

    generated = app.openapi()
    generated["openapi"] = "3.1.0"
    for (_model, path), operation in definitions.items():
        public = copy.deepcopy(operation)
        public.pop("x-livepeer", None)
        public["security"] = [{"HTTPBearer": []}]
        existing = generated["paths"][path]["post"]
        public["responses"] = {**existing["responses"], **public.get("responses", {})}
        generated["paths"][path]["post"] = public
    app.openapi_schema = generated
    return app
