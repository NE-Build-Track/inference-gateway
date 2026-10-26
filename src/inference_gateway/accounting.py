"""Application workflows: record validated usage and reconcile without inference retries."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import aclosing

from inference_gateway.adapters.ledger import SQLiteLedger
from inference_gateway.core.accounting import AccountingSource, Ledger
from inference_gateway.core.backend import Backend
from inference_gateway.core.models import Event, Finished, Generate
from inference_gateway.core.service import GenerationService


class MeteredBackend:
    def __init__(self, backend: Backend, ledger: Ledger) -> None:
        self.service = GenerationService(backend)
        self.ledger = ledger

    async def stream(self, request: Generate) -> AsyncGenerator[Event, None]:
        outcome = "failed"
        try:
            async with aclosing(self.service.stream(request)) as events:
                async for event in events:
                    if isinstance(event, Finished):
                        self.ledger.record_usage(request.request_id, event)
                        outcome = "succeeded"
                    yield event
        except (asyncio.CancelledError, GeneratorExit):
            if outcome != "succeeded":
                outcome = "disconnected"
            raise
        finally:
            self.ledger.outcome(request.request_id, outcome)


async def reconcile_once(ledger: SQLiteLedger, backend: str, source: AccountingSource) -> None:
    for row in ledger.due(backend):
        usage = (
            Finished(
                "stop", row["input_tokens"], row["output_tokens"], row["cached_input_tokens"] or 0
            )
            if row["input_tokens"] is not None
            else None
        )
        try:
            value = await asyncio.wait_for(source.observe(row["id"], row["job_id"], usage), 10)
            ledger.observation(row["id"], value)
        except Exception:
            ledger.reconcile_failed(row["id"])
            logging.getLogger("inference_gateway").warning(
                "accounting_reconciliation_failed request_id=%s", row["id"]
            )


async def reconciliation_loop(ledger: SQLiteLedger, backend: str, source: AccountingSource) -> None:
    while True:
        try:
            await reconcile_once(ledger, backend, source)
        except Exception:
            # Durable due rows survive transient database failures as well as backend failures.
            logging.getLogger("inference_gateway").warning("accounting_worker_unavailable")
        await asyncio.sleep(5)
