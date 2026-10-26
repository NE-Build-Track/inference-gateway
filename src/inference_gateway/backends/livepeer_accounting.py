"""Project the engine's accounting through public APIs, without owning its ledger."""

from uuid import UUID

from livepeer_builder import ActorApi, ActorContext, NotFound

from inference_gateway.core.accounting import Observation
from inference_gateway.core.models import Finished


class LivepeerAccounting:
    def __init__(self, engine: ActorApi, actor: ActorContext) -> None:
        self.engine = engine
        self.actor = actor

    async def observe(
        self, request_id: str, job_id: str | None, usage: Finished | None
    ) -> Observation:
        try:
            if job_id:
                job = await self.engine.jobs.get(self.actor, UUID(job_id))
            else:
                before = None
                job = None
                while True:
                    page = await self.engine.jobs.list_jobs(self.actor, limit=100, before=before)
                    job = next((j for j in page if j.operation_ref == request_id), None)
                    if job or len(page) < 100:
                        break
                    before = min(j.created_at for j in page)
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
