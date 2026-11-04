"""Identity, retail pricing, and the backend-neutral wholesale observation port."""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any, Protocol

from inference_gateway.core.models import Finished

SCOPES = frozenset({"inference", "reports", "admin"})
QUANTUM = Decimal("0.000000001")

# Observations that further refreshes cannot improve, apart from a late correction.
SETTLED_STATUSES = frozenset({"none", "observed", "corrected", "unavailable"})
REFRESH_FLOOR = 60.0
SETTLED_FOR = 86400.0
UNRESOLVED_FOR = 7 * 86400.0
PAUSED = 253402300799.0  # Waits for an operator's explicit redrive.


def next_refresh(now: float, created: float, settled: bool) -> float:
    """Refresh a request often while it is young, when corrections are likeliest, then stop.

    The interval is a tenth of the request's age, and never under a minute. A settled request is
    checked for corrections for a day, and an unresolved one for a week.
    """
    age = max(0.0, now - created)
    if age >= (SETTLED_FOR if settled else UNRESOLVED_FOR):
        return PAUSED
    return now + max(REFRESH_FLOOR, age / 10)


@dataclass(frozen=True)
class Principal:
    user_id: str
    key_id: str
    scopes: frozenset[str]

    def permits(self, scope: str) -> bool:
        return scope in self.scopes or "admin" in self.scopes


@dataclass(frozen=True)
class Price:
    version: str
    model: str
    currency: str
    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal | None = None

    def __post_init__(self) -> None:
        if (
            not self.version
            or not self.model
            or len(self.currency) != 3
            or not self.currency.isascii()
            or not self.currency.isalpha()
        ):
            raise ValueError("Price requires version, model and three-letter currency")
        for rate in (
            self.input_per_million,
            self.output_per_million,
            self.cached_input_per_million or self.input_per_million,
        ):
            if not rate.is_finite() or rate < 0 or rate > Decimal("1000000000"):
                raise ValueError("Invalid retail rate")

    def charge(self, usage: Finished) -> Decimal:
        if (
            usage.input_tokens < 0
            or usage.output_tokens < 0
            or not 0 <= usage.cached_input_tokens <= usage.input_tokens
        ):
            raise ValueError("Negative usage")
        with localcontext() as context:
            context.prec = 50
            return (
                (
                    Decimal(usage.input_tokens - usage.cached_input_tokens) * self.input_per_million
                    + Decimal(usage.cached_input_tokens)
                    * (
                        self.cached_input_per_million
                        if self.cached_input_per_million is not None
                        else self.input_per_million
                    )
                    + Decimal(usage.output_tokens) * self.output_per_million
                )
                / Decimal(1000000)
            ).quantize(QUANTUM, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class Observation:
    """A dated derived view, never a claim of final payment settlement."""

    status: str
    job_id: str | None = None
    amount: str | None = None
    currency: str | None = None
    usage: Finished | None = None
    attempts: tuple[dict[str, Any], ...] = ()
    basis: str = "backend_reported"
    usage_reported: bool = False
    quantities: dict[str, int] = field(default_factory=dict)
    usage_source: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {
            "none",
            "pending",
            "observed",
            "corrected",
            "unknown",
            "unavailable",
        }:
            raise ValueError("Invalid accounting observation status")
        if self.amount is not None:
            amount = Decimal(self.amount)
            if not amount.is_finite() or amount < 0 or not self.currency:
                raise ValueError("Invalid wholesale amount")
        if any(v < 0 for v in self.quantities.values()):
            raise ValueError("Invalid wholesale usage")


class AccountingSource(Protocol):
    async def observe(
        self, request_id: str, job_id: str | None, usage: Finished | None, created: float
    ) -> Observation:
        """Read/reconcile accounting only; never run inference or invent a zero cost.

        `created` is when the gateway admitted the request, so a job search can stay near it.
        """
        ...


class Ledger(Protocol):
    def record_usage(self, request_id: str, usage: Finished) -> None: ...
    def outcome(self, request_id: str, status: str) -> None: ...
