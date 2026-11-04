"""Application workflows: record validated usage and reconcile without inference retries."""

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from typing import Any

import anyio

from inference_gateway.adapters.ledger import SQLiteLedger
from inference_gateway.adapters.ledger_writer import LedgerWriter
from inference_gateway.core.accounting import AccountingSource, Ledger
from inference_gateway.core.backend import Backend, UnsupportedResponse
from inference_gateway.core.models import Event, Finished, Generate
from inference_gateway.core.service import GenerationService


async def apply(writer: LedgerWriter | None, write: Callable[..., Any], *args: Any) -> None:
    """Write through the writer thread when there is one; otherwise write here."""
    if writer is None:
        write(*args)
    else:
        await writer.write(write, *args)


class MeteredBackend:
    def __init__(
        self, backend: Backend, ledger: Ledger, writer: LedgerWriter | None = None
    ) -> None:
        self.service = GenerationService(backend)
        self.ledger = ledger
        self.writer = writer

    async def stream(self, request: Generate) -> AsyncGenerator[Event, None]:
        outcome = "failed"
        try:
            async with aclosing(self.service.stream(request)) as events:
                async for event in events:
                    if isinstance(event, Finished):
                        await apply(
                            self.writer, self.ledger.record_usage, request.request_id, event
                        )
                        outcome = "succeeded"
                    yield event
        except UnsupportedResponse as exc:
            if exc.usage is not None:
                await apply(self.writer, self.ledger.record_usage, request.request_id, exc.usage)
            raise
        except (asyncio.CancelledError, GeneratorExit):
            if outcome != "succeeded":
                outcome = "disconnected"
            raise
        finally:
            # The outcome is queued before this waits, and the shield lets a cancelled stream
            # still wait for it, so the request is never left running.
            with anyio.CancelScope(shield=True):
                await apply(self.writer, self.ledger.outcome, request.request_id, outcome)


async def reconcile_once(
    ledger: SQLiteLedger,
    backend: str,
    source: AccountingSource,
    writer: LedgerWriter | None = None,
) -> None:
    for row in ledger.due(backend):
        usage = (
            Finished(
                "stop", row["input_tokens"], row["output_tokens"], row["cached_input_tokens"] or 0
            )
            if row["input_tokens"] is not None
            else None
        )
        try:
            value = await asyncio.wait_for(
                source.observe(row["id"], row["job_id"], usage, row["created"]), 10
            )
            await apply(writer, ledger.observation, row["id"], value)
        except Exception:
            await apply(writer, ledger.reconcile_failed, row["id"])
            logging.getLogger("inference_gateway").warning(
                "accounting_reconciliation_failed request_id=%s", row["id"]
            )


async def reconciliation_loop(
    ledger: SQLiteLedger,
    backend: str,
    source: AccountingSource,
    writer: LedgerWriter | None = None,
) -> None:
    while True:
        try:
            await reconcile_once(ledger, backend, source, writer)
        except Exception:
            # Durable due rows survive transient database failures as well as backend failures.
            logging.getLogger("inference_gateway").warning("accounting_worker_unavailable")
        await asyncio.sleep(5)
