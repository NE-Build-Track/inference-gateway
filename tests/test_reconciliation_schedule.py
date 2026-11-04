"""Reconciliation settles: refreshes slow with age, stop, and find jobs near admission time."""

import sqlite3
import time
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from livepeer_builder.contracts import Fees, JobCost

from inference_gateway.accounting import reconcile_once
from inference_gateway.adapters.ledger import SQLiteLedger
from inference_gateway.backends.livepeer_accounting import LivepeerAccounting
from inference_gateway.core.accounting import PAUSED, Observation, Price, next_refresh
from inference_gateway.core.models import Finished

DAY = 86400.0
NOW = 2_000_000_000.0


@pytest.mark.parametrize(
    ("age", "settled", "expected"),
    [
        (0, True, NOW + 60),
        (-30, False, NOW + 60),  # Clock skew: a request from the future is treated as new.
        (300, True, NOW + 60),  # Never more often than once a minute.
        (3600, True, NOW + 360),  # A tenth of its age.
        (DAY - 1, True, NOW + (DAY - 1) / 10),
        (DAY, True, PAUSED),  # A settled request is checked for corrections for a day.
        (DAY, False, NOW + DAY / 10),
        (7 * DAY - 1, False, NOW + (7 * DAY - 1) / 10),
        (7 * DAY, False, PAUSED),  # An unresolved one waits for an operator after a week.
    ],
)
def test_refresh_interval_grows_with_age_then_stops(age, settled, expected):
    assert next_refresh(NOW, NOW - age, settled) == pytest.approx(expected)


@pytest.fixture
def ledger(tmp_path):
    book = SQLiteLedger(str(tmp_path / "retail.db"))
    uid = book.create_user("Alice")
    _, secret = book.create_key(uid, "test", frozenset({"inference", "reports"}))
    book.set_price(Price("v1", "fake-model", "usd", Decimal("1"), Decimal("2")))
    book.principal = book.authenticate(secret)
    return book


def admitted(ledger, request_id, age, usage=True):
    ledger.begin(request_id, ledger.principal, "fake-model", "fake", "openai")
    if usage:
        ledger.record_usage(request_id, Finished("stop", 10, 2))
    ledger.outcome(request_id, "succeeded")
    with sqlite3.connect(ledger.path) as db:
        db.execute("UPDATE requests SET created=? WHERE id=?", (time.time() - age, request_id))


def scheduled(ledger, request_id):
    with sqlite3.connect(ledger.path) as db:
        return db.execute(
            "SELECT next_reconcile FROM requests WHERE id=?", (request_id,)
        ).fetchone()[0]


def final(usage_reported=True):
    return Observation(
        "observed",
        "job",
        "0.0001",
        "usd",
        basis="ticket_expected_value",
        usage_reported=usage_reported,
    )


def test_settled_requests_stop_and_unresolved_ones_slow_down(ledger):
    cases = {
        "req_new": (60, final(), lambda t: t == pytest.approx(time.time() + 60, abs=5)),
        "req_old": (2 * DAY, final(), lambda t: t == PAUSED),
        # The engine still lacks the gateway's usage, so the request is not settled yet.
        "req_unreported": (2 * DAY, final(usage_reported=False), lambda t: t < PAUSED),
        "req_pending": (2 * DAY, Observation("pending"), lambda t: t < PAUSED),
        "req_lost": (8 * DAY, Observation("unknown"), lambda t: t == PAUSED),
    }
    for request_id, (age, observation, check) in cases.items():
        admitted(ledger, request_id, age)
        ledger.observation(request_id, observation)
        assert check(scheduled(ledger, request_id)), request_id
    pending = scheduled(ledger, "req_pending")
    assert pending == pytest.approx(time.time() + 2 * DAY / 10, abs=5)
    ledger.retry_reconciliation("req_old")
    assert [row["id"] for row in ledger.due("fake")] == ["req_old"]
    ledger.observation("req_old", final())
    assert not ledger.due("fake")


def test_a_request_without_usage_settles_once_its_cost_is_final(ledger):
    admitted(ledger, "req_failed", 2 * DAY, usage=False)
    ledger.observation("req_failed", Observation("none"))
    assert scheduled(ledger, "req_failed") == PAUSED


async def test_a_backlog_of_old_requests_drains_then_the_loop_goes_quiet(ledger):
    for n in range(250):
        admitted(ledger, f"req_{n:03}", 2 * DAY)

    class Source:
        calls = 0

        async def observe(self, request_id, job_id, usage, created):
            self.calls += 1
            return Observation(
                "observed", "job", "0.0001", "usd", usage, (), "ticket_expected_value", True
            )

    source = Source()
    for _ in range(4):
        await reconcile_once(ledger, "fake", source)
    assert source.calls == 250  # One last check each, 100 per pass.
    with sqlite3.connect(ledger.path) as db:  # An hour later.
        db.execute("UPDATE requests SET next_reconcile=next_reconcile-3600")
    await reconcile_once(ledger, "fake", source)
    assert source.calls == 250  # Before, every request came due again each minute.


class Jobs:
    """Core's job list: newest first, strictly before a time, in pages."""

    def __init__(self, jobs):
        self.jobs = sorted(jobs, key=lambda j: j.created_at, reverse=True)
        self.pages = 0

    async def list_jobs(self, actor, *, limit=50, before=None):
        self.pages += 1
        return [j for j in self.jobs if before is None or j.created_at < before][:limit]


class Costs:
    async def for_job(self, actor, job_id):
        return JobCost(
            job_id=job_id,
            status="observed",
            fees=Fees(fee_usd=Decimal("0.01")),
            attempts=(),
            first_observed_at=None,
            updated_at=None,
        )


def job(operation_ref, at):
    return SimpleNamespace(
        id=uuid4(),
        operation_ref=operation_ref,
        usage=None,
        state="succeeded",
        created_at=datetime.fromtimestamp(at, UTC),
    )


async def test_a_job_without_a_saved_id_is_searched_for_near_admission_only():
    admitted_at = NOW - 3 * DAY
    history = [job(f"req_other_{n}", NOW - n * 60) for n in range(5000)]  # Three and a half days.
    first, repeat = job("req_target", admitted_at + 2), job("req_target", admitted_at + 9)
    jobs = Jobs([*history, first, repeat])
    source = LivepeerAccounting(SimpleNamespace(jobs=jobs, costs=Costs()), actor=None)
    found = await source.observe("req_target", None, None, admitted_at)
    assert found.job_id == str(repeat.id)  # The newest job for the request.
    assert jobs.pages == 1
    jobs.pages = 0
    missing = await source.observe("req_missing", None, None, admitted_at)
    assert missing.status == "unknown"
    assert jobs.pages == 1  # Before, this paged through all 5,002 jobs.
