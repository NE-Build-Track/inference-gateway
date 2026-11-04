"""Report reads: indexed, paged in SQL, and run off the event loop."""

import asyncio
import base64
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from decimal import Decimal

import httpx
import pytest

from inference_gateway.adapters.ledger import SQLiteLedger
from inference_gateway.app import create_app
from inference_gateway.config import Settings
from inference_gateway.core.accounting import Price

DAY = 86400
START = float(int(time.time()) - 20 * DAY)


@pytest.fixture
def book(tmp_path):
    ledger = SQLiteLedger(str(tmp_path / "retail.db"))
    alice, bob = ledger.create_user("Alice"), ledger.create_user("Bob")
    keys = {
        "admin": ledger.create_key(alice, "admin", frozenset({"reports", "admin"})),
        "alice": ledger.create_key(alice, "alice", frozenset({"inference", "reports"})),
        "bob": ledger.create_key(bob, "bob", frozenset({"inference", "reports"})),
    }
    for model in ("fake-model", "other-model"):
        ledger.set_price(Price(model, model, "usd", Decimal("1"), Decimal("2")))
    return ledger, keys


def insert(ledger, keys, rows):
    """Write request rows with exact timestamps; `begin` would stamp the current time."""
    with sqlite3.connect(ledger.path) as db:
        db.executemany(
            "INSERT INTO requests(id,user_id,key_id,model,backend,protocol,created,updated,"
            "price_version,outcome,input_tokens,output_tokens,billing_status) "
            "VALUES (?,?,?,?,'fake','openai',?,?,?,?,?,?,?)",
            [
                (
                    rid,
                    *owner(ledger, keys, who),
                    model,
                    created,
                    created,
                    model,
                    outcome,
                    10 if outcome == "succeeded" else None,
                    2 if outcome == "succeeded" else None,
                    "charged" if outcome == "succeeded" else "not_billable",
                )
                for rid, who, model, outcome, created in rows
            ],
        )
        db.executemany(
            "INSERT INTO entries VALUES (?,?,'charge','0.000014000','usd','measured usage',?)",
            [
                ("charge_" + rid, rid, created + 1)
                for rid, _, _, outcome, created in rows
                if outcome == "succeeded"
            ],
        )


def owner(ledger, keys, who):
    principal = ledger.authenticate(keys[who][1])
    return principal.user_id, principal.key_id


def client(ledger, who, keys):
    app = create_app(settings=Settings(accounting_db=ledger.path))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer " + keys[who][1]},
    )


def offset_cursor(params, end, offset):
    """The cursor format gateways issued before request lists moved to (created, id) positions."""
    items = sorted((k, str(v)) for k, v in params.items())
    query = hashlib.sha256(json.dumps(items).encode()).hexdigest()
    raw = json.dumps({"query": query, "end": end, "offset": offset}).encode()
    return base64.urlsafe_b64encode(raw).decode()


async def pages(c, path, params):
    found, page = [], None
    while True:
        result = await c.get(path, params={**params, **({"page": page} if page else {})})
        assert result.status_code == 200, result.text
        body = result.json()
        found.extend(body["data"])
        if not body["has_more"]:
            return found
        page = body["next_page"]


def test_version_3_ledger_gains_report_indexes_and_keeps_its_data(tmp_path):
    path = str(tmp_path / "retail.db")
    ledger = SQLiteLedger(path)
    uid = ledger.create_user("Alice")
    with sqlite3.connect(path) as db:
        db.executescript(
            "DROP INDEX entry_lookup; DROP INDEX report_window; PRAGMA user_version=3;"
        )
    SQLiteLedger(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert {"entry_lookup", "report_window"} <= names
        db.execute("PRAGMA user_version=5")
    assert [u["id"] for u in ledger.users()] == [uid]
    with pytest.raises(ValueError, match="Unsupported accounting schema version"):
        SQLiteLedger(path)


def test_report_reads_search_indexes_instead_of_scanning(book, monkeypatch):
    ledger, keys = book
    insert(
        ledger,
        keys,
        [
            (f"req_{i:03}", ("alice", "bob")[i % 2], "fake-model", "succeeded", START + i)
            for i in range(50)
        ],
    )
    statements = []
    original = SQLiteLedger.connection

    @contextmanager
    def traced(self):
        with original(self) as db:
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(SQLiteLedger, "connection", traced)
    for who in ("admin", "alice"):
        principal = ledger.authenticate(keys[who][1])
        ledger.rows(principal, START, START + DAY)
        ledger.rows(principal, START, START + DAY, filters=[("model", ["fake-model"])], limit=10)
        ledger.rows(principal, START, START + DAY, after=(START + 5, "req_005"), limit=10)
    monkeypatch.undo()
    reads = [s for s in statements if "FROM requests r" in s or "FROM entries WHERE" in s]
    assert len(reads) == 12
    with sqlite3.connect(ledger.path) as db:
        for statement in reads:
            plan = [row[3] for row in db.execute("EXPLAIN QUERY PLAN " + statement)]
            assert not [step for step in plan if step.startswith("SCAN")], (statement, plan)


async def test_request_list_filters_and_pages_in_sql(book):
    ledger, keys = book
    rows = [
        (
            f"req_{i:03}",
            ("alice", "bob")[i % 2],
            ("fake-model", "other-model")[i % 3 == 0],
            ("succeeded", "failed")[i % 5 == 0],
            START + 10 * (i // 2),  # Pairs share a timestamp, so ties must order by ID.
        )
        for i in range(40)
    ]
    insert(ledger, keys, rows)
    every = sorted(rows, key=lambda r: (r[4], r[0]))
    params = {"start_time": START, "end_time": START + DAY, "limit": 4}
    async with client(ledger, "admin", keys) as admin, client(ledger, "bob", keys) as bob:
        listed = await pages(admin, "/v1/gateway/requests", params)
        assert [r["id"] for r in listed] == [r[0] for r in every]
        chosen = {**params, "models": "fake-model", "outcomes": "succeeded"}
        listed = await pages(admin, "/v1/gateway/requests", chosen)
        assert [r["id"] for r in listed] == [
            r[0] for r in every if r[2] == "fake-model" and r[3] == "succeeded"
        ]
        assert all(r["retail_amount"] == "0.000014000" for r in listed)
        listed = await pages(bob, "/v1/gateway/requests", params)
        assert [r["id"] for r in listed] == [r[0] for r in every if r[1] == "bob"]
        old = await admin.get(
            "/v1/gateway/requests",
            params={**params, "page": offset_cursor(params, START + DAY, 4)},
        )
        assert old.status_code == 200
        assert [r["id"] for r in old.json()["data"]] == [r[0] for r in every[4:8]]
        first = (await admin.get("/v1/gateway/requests", params=params)).json()
        misused = await admin.get(
            "/v1/gateway/usage", params={**params, "page": first["next_page"]}
        )
        assert misused.status_code == 400


@pytest.mark.parametrize(
    "path", ["/v1/gateway/usage", "/v1/gateway/costs", "/v1/gateway/wholesale"]
)
async def test_bucket_pages_match_a_single_full_read(book, path):
    ledger, keys = book
    rows = []
    for day in range(10):
        edge = START + day * DAY
        # Rows on, just before and just after each bucket edge, where a page boundary falls.
        for n, created in enumerate((edge, edge - 0.001, edge + 0.5, edge + DAY / 2)):
            if created >= START:
                rows.append(
                    (f"req_{day}_{n}", ("alice", "bob")[n % 2], "fake-model", "succeeded", created)
                )
    insert(ledger, keys, rows)
    params = {"start_time": START, "end_time": START + 10 * DAY, "bucket_width": "1d"}
    async with client(ledger, "admin", keys) as c:
        whole = (await c.get(path, params={**params, "limit": 1000})).json()["data"]
        assert len(whole) == 10
        assert all(bucket["results"] for bucket in whole)  # Every day has requests.
        assert await pages(c, path, {**params, "limit": 3}) == whole


async def test_row_cap_applies_to_one_page_and_not_to_request_lists(book):
    ledger, keys = book
    crowded = [
        (f"req_a{i:05}", "alice", "fake-model", "succeeded", START + i / 1000) for i in range(10001)
    ]
    quiet = [
        (f"req_b{i}", "alice", "fake-model", "succeeded", START + 5 * DAY + i) for i in range(3)
    ]
    insert(ledger, keys, crowded + quiet)
    params = {"start_time": START, "end_time": START + 7 * DAY, "bucket_width": "1d"}
    async with client(ledger, "admin", keys) as c:
        assert (await c.get("/v1/gateway/usage", params={**params, "limit": 7})).status_code == 400
        later = {**params, "limit": 2}
        result = await c.get(
            "/v1/gateway/usage",
            params={**later, "page": offset_cursor(later, START + 7 * DAY, 4)},
        )
        assert result.status_code == 200
        day_five = result.json()["data"][1]
        assert day_five["results"][0]["num_model_requests"] == 3
        listed = await c.get(
            "/v1/gateway/requests",
            params={"start_time": START, "end_time": START + 7 * DAY, "limit": 1000},
        )
        assert listed.status_code == 200 and listed.json()["has_more"]


async def test_reports_run_off_the_event_loop_two_at_a_time(book, monkeypatch):
    ledger, keys = book
    insert(ledger, keys, [("req_1", "alice", "fake-model", "succeeded", START + 1)])
    original, active, peak = SQLiteLedger.rows, [0], [0]

    def slow(self, *args, **kwargs):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        try:
            time.sleep(0.3)  # A large read, holding its thread the way SQLite does.
            return original(self, *args, **kwargs)
        finally:
            active[0] -= 1

    monkeypatch.setattr(SQLiteLedger, "rows", slow)
    gaps, done = [], asyncio.Event()

    async def heartbeat():
        last = time.perf_counter()
        while not done.is_set():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    params = {"start_time": START, "end_time": START + DAY}
    async with client(ledger, "admin", keys) as c:
        beat = asyncio.create_task(heartbeat())
        started = time.perf_counter()
        results = await asyncio.gather(
            *(c.get("/v1/gateway/requests", params=params) for _ in range(4))
        )
        elapsed = time.perf_counter() - started
        done.set()
        await beat
    assert [r.status_code for r in results] == [200] * 4
    assert peak[0] == 2
    assert elapsed >= 0.6  # Four reads, two at a time.
    assert max(gaps) < 0.15  # The loop kept running throughout.
