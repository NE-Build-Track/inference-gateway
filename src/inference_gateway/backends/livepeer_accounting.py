"""Project the engine's accounting through public APIs, without owning its ledger."""

from datetime import UTC, datetime
from uuid import UUID

from livepeer_builder import ActorApi, ActorContext, NotFound

from inference_gateway.core.accounting import Observation
from inference_gateway.core.models import Finished

# Margins around the gateway's admission time for finding a job with no saved ID: clock skew
# before it, and dispatch delay after it, including a repeat after a refused payment.
SEARCH_BEFORE = 60.0
SEARCH_AFTER = 600.0


class LivepeerAccounting:
    def __init__(self, engine: ActorApi, actor: ActorContext) -> None:
        self.engine = engine
        self.actor = actor

    async def observe(
        self, request_id: str, job_id: str | None, usage: Finished | None, created: float
    ) -> Observation:
        try:
            if job_id:
                job = await self.engine.jobs.get(self.actor, UUID(job_id))
            else:
                # The engine creates a request's jobs just after the gateway admits it, so search
                # only near that time, newest first, so a repeated dispatch finds the latest job.
                earliest = datetime.fromtimestamp(created - SEARCH_BEFORE, UTC)
                before = datetime.fromtimestamp(created + SEARCH_AFTER, UTC)
                job = None
                while True:
                    page = await self.engine.jobs.list_jobs(self.actor, limit=100, before=before)
                    job = next(
                        (
                            j
                            for j in page
                            if j.operation_ref == request_id and j.created_at >= earliest
                        ),
                        None,
                    )
                    if job or len(page) < 100:
                        break
                    before = min(j.created_at for j in page)
                    if before < earliest:
                        break
                if job is None:
                    return Observation("unknown")
            assert job is not None
            # Usage reporting is idempotent by read-before-write; the engine owns its write guard.
            if usage and job.usage is None and job.state == "succeeded":
                job = await self.engine.jobs.report_usage(
                    self.actor,
                    job.id,
                    {
                        "prompt_tokens": usage.input_tokens,
                        "completion_tokens": usage.output_tokens,
                        "cached_prompt_tokens": usage.cached_input_tokens,
                    },
                )
            measured = None
            if job.usage:
                q = job.usage.quantities
                if "prompt_tokens" in q and "completion_tokens" in q:
                    measured = Finished(
                        "stop",
                        q["prompt_tokens"],
                        q["completion_tokens"],
                        q.get("cached_prompt_tokens", 0),
                    )
            cost = await self.engine.costs.for_job(self.actor, job.id)
            amount = (
                str(cost.fees.fee_usd)
                if cost.status in {"none", "observed", "corrected"}
                and cost.fees.fee_usd is not None
                else None
            )
            attempts = tuple(
                {
                    "number": a.attempt_number,
                    "classification": a.cost_class,
                    "amount_usd": str(a.fees.fee_usd) if a.fees.fee_usd is not None else None,
                    "amount_eth": str(a.fees.fee_eth),
                    "usage_rows": a.fees.row_count,
                }
                for a in cost.attempts
            )
            return Observation(
                cost.status,
                str(job.id),
                amount,
                "usd" if amount is not None else None,
                measured,
                attempts,
                "ticket_expected_value",
                job.usage is not None,
                dict(job.usage.quantities) if job.usage else {},
                job.usage.source if job.usage else None,
            )
        except NotFound:
            return Observation("unknown", job_id=job_id)
