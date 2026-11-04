"""ASGI delivery with one owner for the native deadline, disconnect and cleanup."""

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from inference_gateway.adapters.ledger import SQLiteLedger
from inference_gateway.adapters.ledger_writer import LedgerWriter
from inference_gateway.core.backend import BackendError, QuotaExhausted
from inference_gateway.core.native import NativeBackend, NativeRequest
from inference_gateway.native import forward


class NativeHTTPResponse(Response):
    def __init__(
        self,
        backend: NativeBackend,
        request: NativeRequest,
        *,
        ledger: SQLiteLedger | None,
        deadline_seconds: float,
        failure: Callable[[str, int], Response],
        writer: LedgerWriter | None = None,
        started: float | None = None,
    ) -> None:
        super().__init__(content=b"")
        self.backend, self.request, self.ledger, self.writer = backend, request, ledger, writer
        self.deadline_seconds, self.failure = deadline_seconds, failure
        self.started = time.monotonic() if started is None else started
        self.status: int | None = None
        self.outcome = "failed"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def watch() -> None:
            while (await receive())["type"] != "http.disconnect":
                pass

        work = asyncio.create_task(self.serve(scope, receive, send))
        watcher = asyncio.create_task(watch())
        try:
            await asyncio.wait({work, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if work.done():
                await work
        finally:
            watcher.cancel()
            if not work.done():
                work.cancel()
            await asyncio.gather(work, watcher, return_exceptions=True)
            # Here, not in `serve`: a finished response cancels `serve` once the client has every
            # byte, and that cancellation would interrupt this wait.
            try:
                if self.writer is not None:
                    await self.writer.flush()  # This request's writes land before it completes.
            finally:
                # Timing and outcome only: never bodies, arguments or backend errors.
                logging.getLogger("inference_gateway").info(
                    json.dumps(
                        {
                            "event": "native_request_finished",
                            "request_id": self.request.request_id,
                            "endpoint": self.request.endpoint,
                            "status": self.status,
                            "outcome": self.outcome,
                            "elapsed_s": time.monotonic() - self.started,
                        }
                    )
                )

    def record(self, write: Callable[..., object] | None) -> Callable[..., None]:
        """Queue a write on the writer thread, apply it here without one, or drop it."""
        writer = self.writer

        def queued(*args: Any) -> None:
            if write is None:
                return
            if writer is None:
                write(*args)
            else:
                writer.send(write, *args)

        return queued

    async def serve(self, scope: Scope, receive: Receive, send: Send) -> None:
        started = False
        ledger = self.ledger
        persist_outcome = self.record(ledger.outcome if ledger else None)

        def record_outcome(request_id: str, value: str) -> None:
            self.outcome = value
            persist_outcome(request_id, value)

        try:
            async with forward(
                self.backend,
                self.request,
                bind_job=self.record(ledger.bind_job if ledger else None),
                record_usage=self.record(ledger.record_usage if ledger else None),
                record_outcome=record_outcome,
                deadline_seconds=self.deadline_seconds,
            ) as delivery:
                self.status = delivery.status_code
                headers = [
                    (k.lower().encode("ascii"), v.encode("latin-1"))
                    for k, v in delivery.headers
                    if k.lower() not in {"x-request-id", "request-id"}
                ]
                headers += [
                    (b"x-request-id", self.request.request_id.encode()),
                    (b"request-id", self.request.request_id.encode()),
                ]
                if delivery.status_code >= 400:
                    # SDK default retries would repeat a paid request as a new operation.
                    headers.append((b"x-should-retry", b"false"))
                if self.request.streaming:
                    # Resolve failures before sending headers whenever possible.
                    first = await anext(delivery.body, None)
                    await send(
                        {
                            "type": "http.response.start",
                            "status": delivery.status_code,
                            "headers": headers,
                        }
                    )
                    started = True
                    if first is not None:
                        await send({"type": "http.response.body", "body": first, "more_body": True})
                    async for chunk in delivery.body:
                        await send({"type": "http.response.body", "body": chunk, "more_body": True})
                else:
                    body = bytearray()
                    async for chunk in delivery.body:
                        if len(body) + len(chunk) > 8 * 1024 * 1024:
                            raise BackendError("Native response exceeds limit")
                        body.extend(chunk)
                    await send(
                        {
                            "type": "http.response.start",
                            "status": delivery.status_code,
                            "headers": headers,
                        }
                    )
                    started = True
                    await send(
                        {"type": "http.response.body", "body": bytes(body), "more_body": True}
                    )
                await send({"type": "http.response.body", "body": b"", "more_body": False})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if started:
                # Abort, without inventing or appending provider events or logging raw exceptions.
                raise RuntimeError("Native response delivery failed") from None
            status = (
                504
                if isinstance(exc, TimeoutError)
                else exc.status_code
                if isinstance(exc, BackendError)
                else 502
            )
            message = (
                exc.public_message
                if isinstance(exc, QuotaExhausted)
                else "Generation deadline exceeded"
                if status == 504
                else "Native backend unavailable"
                if status == 503
                else "Native backend response failed"
            )
            self.status = status
            await self.failure(message, status)(scope, receive, send)
