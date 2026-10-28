"""Native forwarding lifecycle, reusable by an HTTP adapter with a raw transport."""

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import anyio

from inference_gateway.adapters.native_usage import UsageObserver
from inference_gateway.core.backend import BackendError
from inference_gateway.core.models import Finished
from inference_gateway.core.native import NativeBackend, NativeRequest


@dataclass(frozen=True)
class NativeDelivery:
    status_code: int
    headers: tuple[tuple[str, str], ...]
    body: AsyncGenerator[bytes, None]


def response_headers(headers: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    # Explicit public headers, never cookies, internal credentials, or payment control fields.
    allowed = {"content-type", "retry-after", "anthropic-version", "x-request-id", "request-id"}
    connection = {
        p.strip().lower() for k, v in headers if k.lower() == "connection" for p in v.split(",")
    }
    return tuple((k, v) for k, v in headers if k.lower() in allowed - connection)


@asynccontextmanager
async def forward(
    backend: NativeBackend,
    request: NativeRequest,
    *,
    bind_job: Callable[[str, str], None],
    record_usage: Callable[[str, Finished], None],
    record_outcome: Callable[[str, str], None],
    deadline_seconds: float = 300,
) -> AsyncIterator[NativeDelivery]:
    """Caller authenticates/begins accounting first and owns this context through send.

    Includes open, consumption and downstream backpressure in one deadline. Errors
    after headers abort delivery; never invent provider SSE error events. No retries.
    """
    outcome = "failed"
    stream = None
    body = None
    iterator = None
    recorded = False
    observer = UsageObserver(request.endpoint, request.streaming, limit=8 * 1024 * 1024)
    try:
        async with asyncio.timeout(deadline_seconds):
            stream = await backend.open(request)
            bind_job(request.request_id, stream.job_id)
            if any(
                k.lower() == "content-encoding" and v.lower() not in {"identity", ""}
                for k, v in stream.headers
            ):
                raise BackendError("Encoded native responses require an admitted decoding profile")
            iterator = stream.__aiter__()

            def persist() -> None:
                nonlocal recorded
                if observer.measured is not None and not recorded:
                    record_usage(request.request_id, observer.measured)
                    recorded = True

            async def chunks() -> AsyncGenerator[bytes, None]:
                nonlocal outcome
                assert stream is not None and iterator is not None
                async for raw in iterator:
                    if 200 <= stream.status_code < 300:
                        try:
                            observer.feed(raw)
                        finally:
                            # Preserve usage even if a later event in this chunk is invalid.
                            persist()
                    yield raw
                if 200 <= stream.status_code < 300:
                    try:
                        observer.finish(require_success=False)
                    finally:
                        persist()
                    await stream.complete()
                    outcome = "failed" if observer.failed else "succeeded"
                    try:
                        if observer.measured is not None:
                            await asyncio.wait_for(
                                backend.report_usage(stream.job_id, observer.measured), 5
                            )
                    except Exception:
                        logging.getLogger("inference_gateway").warning(
                            "native_usage_report_failed request_id=%s", request.request_id
                        )
                # Non-2xx bytes are passed through; transport owns paid failure settlement.

            body = chunks()
            yield NativeDelivery(stream.status_code, response_headers(stream.headers), body)
    except (asyncio.CancelledError, GeneratorExit):
        outcome = "disconnected"
        raise
    finally:
        with anyio.CancelScope(shield=True):
            try:
                if stream is not None:
                    try:
                        if outcome != "succeeded":
                            await stream.disconnected()
                    finally:
                        try:
                            if body is not None:
                                await body.aclose()
                        finally:
                            try:
                                if iterator is not None:
                                    await iterator.aclose()
                            finally:
                                await stream.aclose()
            finally:
                record_outcome(request.request_id, outcome)
