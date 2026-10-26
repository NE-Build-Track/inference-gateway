import asyncio
import json
import sqlite3
import time
from decimal import Decimal

import httpx
import pytest
from openai import AsyncOpenAI

from endpoint_gateway.accounting import reconcile_once
from endpoint_gateway.adapters.ledger import SQLiteLedger
from endpoint_gateway.app import create_app
from endpoint_gateway.config import Settings
from endpoint_gateway.core.accounting import Observation, Price
from endpoint_gateway.core.models import Finished


@pytest.fixture
def book(tmp_path):
    ledger = SQLiteLedger(str(tmp_path / "retail.db"))
    uid = ledger.create_user("Alice")
    kid, secret = ledger.create_key(uid, "test", frozenset({"inference", "reports"}))
    ledger.set_price(Price("v1", "fake-model", "usd", Decimal("1"), Decimal("2")))
    return ledger, uid, kid, secret


def begin(book, request_id="req_test"):
    ledger, _, _, secret = book
    principal = ledger.authenticate(secret)
    ledger.begin(request_id, principal, "fake-model", "fake", "openai")
    return principal


def test_key_secrets_revocation_rotation_and_disable(book):
    ledger, uid, kid, secret = book
    assert secret not in json.dumps(ledger.keys())
    with ledger.connection() as db:
        assert secret not in str([tuple(r) for r in db.execute("SELECT * FROM api_keys")])
    new_id, new_secret = ledger.rotate_key(kid)
    assert ledger.authenticate(secret) is None
    assert ledger.authenticate(new_secret).key_id == new_id
    ledger.disable_user(uid)
    assert ledger.authenticate(new_secret) is None
    with pytest.raises(ValueError):
        ledger.create_key(uid, "disabled", frozenset({"inference"}))


def test_expired_keys(book, monkeypatch):
    ledger, uid, _, _ = book
    for invalid in [float("nan"), float("inf"), float("-inf")]:
        with pytest.raises(ValueError):
            ledger.create_key(uid, "invalid", frozenset({"reports"}), invalid)
    now = time.time()
    _, secret = ledger.create_key(uid, "expires", frozenset({"reports"}), now + 10)
    monkeypatch.setattr(time, "time", lambda: now + 11)
    assert ledger.authenticate(secret) is None


def test_price_snapshot_exact_charge_idempotence_and_adjustment(book):
    ledger, *_ = book
    principal = begin(book)
    ledger.set_price(Price("v2", "fake-model", "usd", Decimal("99"), Decimal("99")))
    usage = Finished("stop", 10, 20)
    ledger.record_usage("req_test", usage)
    ledger.record_usage("req_test", usage)
    ledger.adjustment("req_test", Decimal("-0.00001"), "credit", "credit1")
    ledger.adjustment("req_test", Decimal("-0.00001"), "credit", "credit1")
    row = ledger.rows(principal, 0, time.time() + 1)[0]
    assert row["retail_amount"] == "0.000040000"
    assert len(row["entries"]) == 2
    assert row["price_version"] == "v1"
    with pytest.raises(ValueError):
        ledger.record_usage("req_test", Finished("stop", 99, 20))
    with pytest.raises(ValueError):
        ledger.adjustment("req_test", Decimal("-1"), "credit", "credit1")
    with ledger.connection() as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE entries SET amount='0'")


def test_invalid_price_rolls_back_active_version(book):
    ledger, *_ = book
    with pytest.raises(sqlite3.IntegrityError):
        ledger.set_price(Price("v1", "fake-model", "usd", Decimal(9), Decimal(9)))
    assert ledger.prices()[0]["active"] == 1
    for invalid in ["NaN", "Infinity", "-1"]:
        with pytest.raises(ValueError):
            Price("v2", "fake-model", "usd", Decimal(invalid), Decimal(0))


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_registry_generation_usage_and_reports(book, protocol, streaming):
    ledger, _, _, secret = book
    app = create_app(settings=Settings(accounting_db=ledger.path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        body = {
            "model": "fake-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": streaming,
        }
        headers = (
            {"Authorization": "Bearer " + secret}
            if protocol == "openai"
            else {"x-api-key": secret, "anthropic-version": "2023-06-01"}
        )
        if protocol == "anthropic":
            body["max_tokens"] = 100
        result = await client.post(
            "/v1/chat/completions" if protocol == "openai" else "/v1/messages",
            json=body,
            headers=headers,
        )
        assert result.status_code == 200
        report = await client.get(
            "/v1/gateway/requests", params={"start_time": int(time.time()) - 60}, headers=headers
        )
        row = report.json()["data"][0]
        assert row["id"] == result.headers["x-request-id"]
        assert row["outcome"] == "succeeded" and row["billing_status"] == "charged"
        assert row["input_tokens"] == 0 and row["output_tokens"] == len("Echo: hi")
        assert "wholesale_amount" not in row and "job_id" not in row
        assert Decimal(row["retail_amount"]) == Price(
            "v1", "fake-model", "usd", Decimal(1), Decimal(2)
        ).charge(Finished("stop", row["input_tokens"], row["output_tokens"]))


async def test_reporting_access_filters_and_openai_sdk(book):
    ledger, uid, kid, secret = book
    principal = begin(book)
    ledger.record_usage("req_test", Finished("stop", 10, 20))
    ledger.outcome("req_test", "succeeded")
    other = ledger.create_user("Bob")
    _, other_secret = ledger.create_key(other, "bob", frozenset({"reports"}))
    _, inference_secret = ledger.create_key(uid, "infer", frozenset({"inference"}))
    _, admin = ledger.create_key(uid, "admin", frozenset({"admin"}))
    app = create_app(settings=Settings(accounting_db=ledger.path))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        path = "/v1/gateway/requests"
        params = {"start_time": int(time.time()) - 60}
        for key, status, count in [
            (secret, 200, 1),
            (other_secret, 200, 0),
            (inference_secret, 403, None),
            ("bad", 401, None),
        ]:
            result = await client.get(
                path, params=params, headers={"Authorization": "Bearer " + key}
            )
            assert result.status_code == status
            if count is not None:
                assert len(result.json()["data"]) == count
        hidden = await client.get(
            path, params={**params, "user_ids": uid}, headers={"x-api-key": other_secret}
        )
        assert hidden.json()["data"] == []
        assert (
            await client.get("/v1/gateway/wholesale", params=params, headers={"x-api-key": secret})
        ).status_code == 403
        api = AsyncOpenAI(
            base_url="http://test/v1",
            api_key=admin,
            admin_api_key=admin,
            http_client=client,
            max_retries=0,
        )
        report = await api.admin.organization.usage.completions(
            start_time=params["start_time"], group_by=["api_key_id"]
        )
        assert report.data[0].results[0].api_key_id == kid
        assert report.data[0].results[0].input_tokens == 10
        costs = await api.admin.organization.usage.costs(start_time=params["start_time"])
        assert Decimal(str(costs.data[0].results[0].amount.value)) == Decimal("0.00005")
    assert len(ledger.rows(principal, 0, time.time() + 1)) == 1


async def test_anthropic_reports_pagination_and_currency(book):
    ledger, _, _, secret = book
    principal = begin(book)
    ledger.record_usage("req_test", Finished("stop", 10, 20))
    app = create_app(settings=Settings(accounting_db=ledger.path))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"x-api-key": secret},
    ) as c:
        params = {"starting_at": int(time.time()) - 120, "bucket_width": "1m", "limit": 1}
        result = await c.get("/v1/organizations/usage_report/messages", params=params)
        assert result.status_code == 200 and result.json()["has_more"]
        token = result.json()["next_page"]
        second = await c.get(
            "/v1/organizations/usage_report/messages", params={**params, "page": token}
        )
        assert second.status_code == 200
        assert second.json()["data"][0]["starting_at"] != result.json()["data"][0]["starting_at"]
        assert (
            await c.get("/v1/organizations/usage_report/messages", params={**params, "page": "bad"})
        ).status_code == 400
        cost = await c.get(
            "/v1/organizations/cost_report", params={"starting_at": int(time.time()) - 120}
        )
        assert cost.json()["data"][0]["results"][0]["amount"] == "0.005000000"
        assert len(ledger.rows(principal, 0, time.time() + 1)) == 1


async def test_recovery_observations_and_accounting_retry_never_dispatch(book):
    ledger, *_ = book
    principal = begin(book)
    ledger.recover()

    class Source:
        calls = 0

        async def observe(self, request_id, job_id, usage):
            self.calls += 1
            return Observation("pending", job_id="job1", usage=Finished("stop", 10, 20))

    source = Source()
    await reconcile_once(ledger, "fake", source)
    row = ledger.rows(principal, 0, time.time() + 1)[0]
    assert row["outcome"] == "interrupted" and row["retail_amount"] == "0.000050000"
    with ledger.connection() as db:
        db.execute("UPDATE requests SET next_reconcile=0")
    await reconcile_once(ledger, "fake", source)
    assert len(ledger.rows(principal, 0, time.time() + 1)[0]["entries"]) == 1
    assert source.calls == 2


async def test_unknown_usage_not_zero_and_reconcile_failure_retained(book):
    ledger, *_ = book
    principal = begin(book)
    ledger.outcome("req_test", "failed")

    class Source:
        async def observe(self, *args):
            raise RuntimeError("private backend detail")

    await reconcile_once(ledger, "fake", Source())
    row = ledger.rows(principal, 0, time.time() + 1)[0]
    assert row["input_tokens"] is None and row["retail_amount"] is None
    assert row["billing_status"] == "pending"
    with ledger.connection() as db:
        assert db.execute("SELECT reconcile_failures FROM requests").fetchone()[0] == 1


async def test_simultaneous_recording_does_not_duplicate_charge(book):
    ledger, *_ = book
    principal = begin(book)
    await asyncio.gather(
        *(
            asyncio.to_thread(ledger.record_usage, "req_test", Finished("stop", 3, 4))
            for _ in range(8)
        )
    )
    assert len(ledger.rows(principal, 0, time.time() + 1)[0]["entries"]) == 1


def test_cache_pricing_and_currency_separation(book):
    ledger, *_ = book
    ledger.set_price(Price("cache-v1", "fake-model", "eur", Decimal(1), Decimal(2), Decimal("0.1")))
    principal = begin(book)
    ledger.record_usage("req_test", Finished("stop", 100, 10, 80))
    row = ledger.rows(principal, 0, time.time() + 1)[0]
    assert Decimal(row["retail_amount"]) == Decimal("0.000048")
    assert row["currency"] == "eur" and row["cached_input_tokens"] == 80


def test_single_server_lock_and_schema_upgrade(tmp_path):
    from endpoint_gateway.adapters.ledger import SCHEMA

    path = str(tmp_path / "v1.db")
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
    ledger = SQLiteLedger(path)
    with ledger.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
    with ledger.server_lock():
        with pytest.raises(ValueError), SQLiteLedger(path).server_lock():
            pass
    with ledger.server_lock():
        pass


def test_reconciliation_pauses_after_bounded_failures_and_cli_can_redrive(book):
    ledger, *_ = book
    begin(book)
    ledger.outcome("req_test", "failed")
    for _ in range(8):
        ledger.reconcile_failed("req_test")
    assert not ledger.due("fake")
    ledger.retry_reconciliation("req_test")
    assert ledger.due("fake")[0]["reconcile_failures"] == 0


async def test_wholesale_observations_are_admin_only_and_not_retail(book):
    ledger, uid, _, _ = book
    begin(book)
    ledger.record_usage("req_test", Finished("stop", 10, 20))
    ledger.observation(
        "req_test",
        Observation(
            "observed",
            "job1",
            "0.19",
            "usd",
            quantities={"duration_ms": 100},
            usage_source="engine_meter",
            basis="duration",
        ),
    )
    _, admin = ledger.create_key(uid, "admin", frozenset({"admin"}))
    app = create_app(settings=Settings(accounting_db=ledger.path))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"x-api-key": admin}
    ) as c:
        params = {"start_time": int(time.time()) - 60}
        row = (await c.get("/v1/gateway/requests", params=params)).json()["data"][0]
        assert row["wholesale_amount"] == "0.19" and row["retail_amount"] == "0.000050000"
        assert row["wholesale_usage"] == {"duration_ms": 100}
        report = (await c.get("/v1/gateway/wholesale", params=params)).json()
        assert report["data"][0]["results"][0]["exact_amount"] == "0.19"


async def test_cancelled_generation_keeps_pending_accounting(book):
    from endpoint_gateway.accounting import MeteredBackend
    from endpoint_gateway.core.models import BlockStart, Generate, Message, Text

    class Slow:
        async def stream(self, request):
            yield BlockStart(0)
            await asyncio.sleep(60)

    ledger, *_ = book
    principal = begin(book)
    stream = MeteredBackend(Slow(), ledger).stream(
        Generate("fake-model", (Message("user", (Text("x"),)),), request_id="req_test")
    )
    await anext(stream)
    await stream.aclose()
    row = ledger.rows(principal, 0, time.time() + 1)[0]
    assert row["outcome"] == "disconnected" and row["billing_status"] == "pending"
    assert row["retail_amount"] is None


async def test_unknown_charges_are_not_reported_as_zero_and_detail_is_isolated(book):
    ledger, _, _, secret = book
    begin(book)
    other = ledger.create_user("other")
    _, key = ledger.create_key(other, "reader", frozenset({"reports"}))
    app = create_app(settings=Settings(accounting_db=ledger.path))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        headers = {"x-api-key": secret}
        own = await c.get("/v1/gateway/requests/req_test", headers=headers)
        assert own.status_code == 200 and own.json()["retail_amount"] is None
        assert (
            await c.get("/v1/gateway/requests/req_test", headers={"x-api-key": key})
        ).status_code == 404
        result = await c.get(
            "/v1/organization/costs", params={"start_time": int(time.time()) - 10}, headers=headers
        )
        bucket = result.json()["data"][0]
        assert bucket["results"] == [] and bucket["gateway_unresolved_requests"] == 1


async def test_currency_groups_do_not_mix_and_anthropic_rejects_non_usd(book):
    ledger, _, _, secret = book
    begin(book, "usd")
    ledger.record_usage("usd", Finished("stop", 10, 20))
    ledger.set_price(Price("eur-v1", "fake-model", "eur", Decimal(1), Decimal(2)))
    begin(book, "eur")
    ledger.record_usage("eur", Finished("stop", 10, 20))
    app = create_app(settings=Settings(accounting_db=ledger.path))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"x-api-key": secret},
    ) as c:
        params = {"start_time": int(time.time()) - 60}
        native = (await c.get("/v1/gateway/costs", params=params)).json()["data"][0]["results"]
        assert {r["currency"] for r in native} == {"usd", "eur"}
        assert all(Decimal(r["amount"]) == Decimal("0.00005") for r in native)
        assert (await c.get("/v1/organizations/cost_report", params=params)).status_code == 400
