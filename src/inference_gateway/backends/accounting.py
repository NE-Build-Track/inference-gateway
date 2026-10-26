"""Explicit accounting capability for backends without a monetary accounting source."""

from inference_gateway.core.accounting import Observation
from inference_gateway.core.models import Finished


class UnavailableAccounting:
    async def observe(
        self, request_id: str, job_id: str | None, usage: Finished | None
    ) -> Observation:
        return Observation("unavailable", basis="not_provided", usage_reported=True)


class FakeAccounting:
    async def observe(
        self, request_id: str, job_id: str | None, usage: Finished | None
    ) -> Observation:
        return Observation(
            "none",
            job_id=request_id,
            amount="0",
            currency="usd",
            basis="fixture",
            usage_reported=True,
        )
