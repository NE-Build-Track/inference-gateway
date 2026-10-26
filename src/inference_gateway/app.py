"""Application composition root. Own configuration and backend selection here."""

import asyncio
import hmac
import json
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager, nullcontext, suppress
from dataclasses import replace
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

from inference_gateway.accounting import MeteredBackend, reconciliation_loop
from inference_gateway.adapters.http_stream import ManagedStreamingResponse
from inference_gateway.adapters.ledger import SQLiteLedger
from inference_gateway.adapters.reports import attach_reports
from inference_gateway.adapters.responses import (
    Protocol,
    ResponseIdentity,
    anthropic_stream,
    openai_stream,
    response,
    sse,
)
from inference_gateway.adapters.schemas import AnthropicRequest, OpenAIRequest
from inference_gateway.adapters.translate import anthropic_request, openai_request
from inference_gateway.backends.accounting import FakeAccounting, UnavailableAccounting
from inference_gateway.config import LivepeerSettings, Settings
from inference_gateway.core.accounting import AccountingSource
from inference_gateway.core.backend import Backend, BackendError
from inference_gateway.core.models import Event
from inference_gateway.core.service import GenerationService
from inference_gateway.runtime import (
    EngineFactory,
    build_engine,
    livepeer_backend,
    load_livepeer_settings,
    load_settings,
)
from inference_gateway.testing import FakeBackend


class ClientDisconnected(Exception):
    pass


async def while_connected[T](request: Request, work: Awaitable[T]) -> T:
    """Cancel generation even when a nonstream client or pre-header client leaves."""

    async def run() -> T:
        return await work

    async def watch() -> None:
        while True:
            if (await request.receive())["type"] == "http.disconnect":
                return

    task = asyncio.create_task(run())
    watcher = asyncio.create_task(watch())
    try:
        await asyncio.wait({task, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            return await task
        raise ClientDisconnected()
    finally:
        watcher.cancel()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, watcher, return_exceptions=True)


def error_payload(protocol: Protocol, message: str, kind: str) -> dict[str, Any]:
    error = {"message": message, "type": kind}
    if protocol == "anthropic":
        return {"type": "error", "error": error}
    return {"error": {**error, "param": None, "code": kind}}


def failure(protocol: Protocol, message: str, status: int, request_id: str) -> JSONResponse:
    kinds = {
        400: "invalid_request_error",
        401: "authentication_error",
        403: "permission_error",
        409: "invalid_request_error",
        499: "api_error",
        502: "api_error",
        503: "api_error",
        504: "timeout_error",
    }
    return JSONResponse(
        error_payload(protocol, message, kinds[status]),
        status_code=status,
        headers={
            "x-request-id": request_id,
            "request-id": request_id,
            "x-should-retry": "false",
        },
    )


async def deadline_stream(
    events: AsyncGenerator[Event, None], deadline: float
) -> AsyncGenerator[Event, None]:
    async with aclosing(events):
        while True:
            try:
                remaining = max(0, deadline - asyncio.get_running_loop().time())
                item = await asyncio.wait_for(anext(events), timeout=remaining)
            except StopAsyncIteration:
                return
            yield item


def create_app(
    backend: Backend | None = None,
    settings: Settings | None = None,
    *,
    livepeer_settings: LivepeerSettings | None = None,
    engine_factory: EngineFactory | None = None,
    accounting_source: AccountingSource | None = None,
) -> FastAPI:
    """Borrow injected backends; own configured Builder Core through ASGI lifespan."""
    config = settings if settings is not None else load_settings()
    owns_engine = backend is None and config.backend == "livepeer"
    ledger = SQLiteLedger(config.accounting_db) if config.accounting_db else None
    selected_backend = backend if backend is not None else FakeBackend()

    def compose(selected: Backend) -> GenerationService:
        return GenerationService(MeteredBackend(selected, ledger) if ledger else selected)

    service = None if owns_engine else compose(selected_backend)
    source: AccountingSource = accounting_source or (
        FakeAccounting() if backend is None and not owns_engine else UnavailableAccounting()
    )

    def service_ready() -> bool:
        return service is not None

    ready: Callable[[], bool] = service_ready
    logger = logging.getLogger("inference_gateway")

    @asynccontextmanager
    async def resources(app: FastAPI) -> AsyncIterator[None]:
        nonlocal service, ready, source
        if ledger:
            ledger.recover()

        @asynccontextmanager
        async def accounting_worker() -> AsyncIterator[None]:
            worker = (
                asyncio.create_task(reconciliation_loop(ledger, config.backend, source))
                if ledger
                else None
            )
            try:
                yield
            finally:
                if worker:
                    worker.cancel()
                    with suppress(asyncio.CancelledError):
                        await worker

        if owns_engine:
            options = livepeer_settings or load_livepeer_settings(config)
            async with livepeer_backend(
                options,
                engine_factory or build_engine,
                ledger.record_usage if ledger else None,
                ledger.bind_job if ledger else None,
            ) as live:
                service = compose(live)
                source = accounting_source or live.accounting
                ready = live.ready
                try:
                    async with accounting_worker():
                        yield
                finally:
                    service = None
                    ready = service_ready
        else:
            async with accounting_worker():
                yield

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        with ledger.server_lock() if ledger else nullcontext():
            async with resources(app):
                yield

    app = FastAPI(title="Agent-compatible endpoint gateway", version="0.1.0", lifespan=lifespan)

    if ledger:
        attach_reports(app, ledger)

    @app.get("/readyz")
    async def readiness() -> JSONResponse:
        ok = ready()
        return JSONResponse(
            {"status": "ready" if ok else "unavailable"}, status_code=200 if ok else 503
        )

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async def generate(request: Request, protocol: Protocol) -> JSONResponse | StreamingResponse:
        request_id = "req_" + uuid4().hex
        started = time.monotonic()
        headers = {"x-request-id": request_id, "request-id": request_id}
        key = (
            request.headers.get("authorization", "").removeprefix("Bearer ")
            if protocol == "openai"
            else request.headers.get("x-api-key", "")
        )
        principal = ledger.authenticate(key) if ledger else None
        if ledger and principal is None:
            return failure(protocol, "Invalid API key", 401, request_id)
        if principal and not principal.permits("inference"):
            return failure(protocol, "Insufficient inference permission", 403, request_id)
        if config.api_key is not None and not hmac.compare_digest(
            key.encode(), config.api_key.encode()
        ):
            return failure(protocol, "Invalid API key", 401, request_id)
        if protocol == "anthropic" and request.headers.get("anthropic-version") != "2023-06-01":
            return failure(protocol, "Use anthropic-version: 2023-06-01", 400, request_id)
        try:
            data = await request.json()
            include_usage = False
            if protocol == "openai":
                body = OpenAIRequest.model_validate(data)
                command, streaming = openai_request(body), body.stream
                include_usage = (
                    body.stream_options is not None and body.stream_options.include_usage
                )
            else:
                anthropic_body = AnthropicRequest.model_validate(data)
                command, streaming = anthropic_request(anthropic_body), anthropic_body.stream
        except (ValueError, ValidationError):
            # Do not reflect potentially sensitive prompt/tool values in errors.
            return failure(
                protocol, "Invalid or unsupported request; see compatibility docs", 400, request_id
            )
        command = replace(command, request_id=request_id)
        if service is None:
            return failure(protocol, "Generation backend unavailable", 503, request_id)
        if ledger and principal:
            try:
                ledger.begin(request_id, principal, command.model, config.backend, protocol)
            except ValueError:
                return failure(protocol, "Model has no configured retail price", 400, request_id)
        identity = ResponseIdentity(
            ("chatcmpl_" if protocol == "openai" else "msg_") + uuid4().hex,
            command.model,
            int(time.time()),
        )
        try:
            if not streaming:
                result = await while_connected(
                    request, asyncio.wait_for(service.complete(command), config.timeout_seconds)
                )
                logger.info(
                    json.dumps(
                        {
                            "event": "generation_finished",
                            "request_id": request_id,
                            "protocol": protocol,
                            "elapsed_s": time.monotonic() - started,
                        }
                    )
                )
                return JSONResponse(response(result, identity, protocol), headers=headers)
            deadline = asyncio.get_running_loop().time() + config.timeout_seconds
            events = deadline_stream(service.stream(command), deadline)
            # Fail before committing HTTP 200 if the backend cannot start.
            first = await while_connected(request, anext(events))
        except ClientDisconnected:
            return failure(protocol, "Client disconnected", 499, request_id)
        except BackendError as exc:
            return failure(protocol, exc.public_message, exc.status_code, request_id)
        except TimeoutError:
            return failure(protocol, "Generation deadline exceeded", 504, request_id)
        except Exception:
            logger.error(json.dumps({"event": "backend_failed", "request_id": request_id}))
            return failure(protocol, "Backend generation failed", 502, request_id)

        async def replay() -> AsyncGenerator[Event, None]:
            async with aclosing(events):
                yield first
                async for event in events:
                    yield event

        async def render() -> AsyncGenerator[str, None]:
            wire = (
                openai_stream(replay(), identity, include_usage)
                if protocol == "openai"
                else anthropic_stream(replay(), identity)
            )
            outcome = "cancelled"
            try:
                async with aclosing(wire):
                    async for frame in wire:
                        yield frame
                outcome = "finished"
            except Exception:
                outcome = "failed"
                payload = error_payload(protocol, "Backend stream failed", "api_error")
                yield sse(payload, "error" if protocol == "anthropic" else None)
            finally:
                await events.aclose()
                logger.info(
                    json.dumps(
                        {
                            "event": "stream_" + outcome,
                            "request_id": request_id,
                            "protocol": protocol,
                            "elapsed_s": time.monotonic() - started,
                        }
                    )
                )

        return ManagedStreamingResponse(
            render(),
            events.aclose,
            headers={**headers, "Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(request: Request) -> JSONResponse | StreamingResponse:
        return await generate(request, "openai")

    @app.post("/v1/messages", response_model=None)
    async def messages(request: Request) -> JSONResponse | StreamingResponse:
        return await generate(request, "anthropic")

    return app
