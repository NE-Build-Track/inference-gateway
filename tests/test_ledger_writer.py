"""Ledger writes leave the event loop, keep their order, and survive cancellation."""

import asyncio
import json
import logging
import sqlite3
import time
from decimal import Decimal

import httpx
import pytest

from inference_gateway.accounting import MeteredBackend
from inference_gateway.adapters.ledger import SQLiteLedger
from inference_gateway.adapters.ledger_writer import LedgerWriter
from inference_gateway.app import create_app
from inference_gateway.config import Settings
from inference_gateway.core.accounting import Price
from inference_gateway.core.models import BlockStart, Generate, Message, Text

CHAT = {"model": "fake-model", "messages": [{"role": "user", "content": "hi"}]}


@pytest.fixture
def book(tmp_path):
    ledger = SQLiteLedger(str(tmp_path / "retail.db"))
    uid = ledger.create_user("Alice")
    _, secret = ledger.create_key(uid, "test", frozenset({"inference", "reports"}))
    ledger.set_price(Price("v1", "fake-model", "usd", Decimal("1"), Decimal("2")))
    return ledger, secret


def requests(ledger):
    with sqlite3.connect(ledger.path) as db:
        return db.execute("SELECT outcome, billing_status FROM requests").fetchall()


async def test_writes_apply_in_order_and_survive_a_cancelled_wait():
    writer, applied = LedgerWriter(), []

    def slow(name):
        time.sleep(0.2)
        applied.append(name)

    writer.send(slow, "first")
    waiting = asyncio.create_task(writer.write(applied.append, "second"))
    await asyncio.sleep(0.05)
    waiting.cancel()  # The caller gives up; the write must not.
    with pytest.raises(asyncio.CancelledError):
        await waiting
    writer.send(applied.append, "third")
    await writer.flush()
    assert applied == ["first", "second", "third"]
    assert await writer.write(lambda: 7) == 7
    writer.close()
    assert await writer.write(lambda: "restarted") == "restarted"  # A new lifespan reuses it.
    writer.close()


async def test_write_errors_reach_the_caller_and_queued_failures_are_logged_safely(caplog):
    writer = LedgerWriter()

    def broken(request_id):
        raise ValueError("Conflicting usage for a secret prompt")

    with pytest.raises(ValueError):
        await writer.write(broken, "req_1")
    caplog.set_level(logging.WARNING, logger="inference_gateway")
    writer.send(broken, "req_2")
    await writer.flush()
    writer.close()
    logged = [json.loads(r.getMessage()) for r in caplog.records]
    assert logged == [{"event": "ledger_write_failed", "write": "broken", "request_id": "req_2"}]
    assert "secret" not in caplog.text


async def test_slow_ledger_writes_no_longer_stall_the_event_loop(book, monkeypatch):
    ledger, secret = book
    for name in ("begin", "record_usage", "outcome"):
        original = getattr(SQLiteLedger, name)

        def slow(self, *args, _original=original):
            time.sleep(0.2)  # A slow disk commit, holding its thread as SQLite does.
            return _original(self, *args)

        monkeypatch.setattr(SQLiteLedger, name, slow)
    gaps, done = [], asyncio.Event()

    async def heartbeat():
        last = time.perf_counter()
        while not done.is_set():
            await asyncio.sleep(0.01)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now

    app = create_app(settings=Settings(accounting_db=ledger.path))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer " + secret},
    ) as client:
        beat = asyncio.create_task(heartbeat())
        result = await client.post("/v1/chat/completions", json=CHAT)
        done.set()
        await beat
    assert result.status_code == 200
    assert max(gaps) < 0.15  # Three 0.2 s writes ran, and the loop kept going.
    assert requests(ledger) == [("succeeded", "charged")]  # All applied before the reply.


async def test_a_request_cancelled_while_its_begin_waits_is_not_left_running(book, monkeypatch):
    ledger, secret = book
    original = SQLiteLedger.begin

    def slow(self, *args):
        time.sleep(0.3)
        return original(self, *args)

    monkeypatch.setattr(SQLiteLedger, "begin", slow)
    app = create_app(settings=Settings(accounting_db=ledger.path))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer " + secret},
    ) as client:
        call = asyncio.create_task(client.post("/v1/chat/completions", json=CHAT))
        await asyncio.sleep(0.1)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
    for _ in range(50):
        if requests(ledger):
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.1)
    assert requests(ledger) == [("disconnected", "pending")]


async def test_a_cancelled_stream_records_its_outcome_through_the_writer(book):
    ledger, secret = book

    class Slow:
        async def stream(self, request):
            yield BlockStart(0)
            await asyncio.sleep(60)

    writer = LedgerWriter()
    principal = ledger.authenticate(secret)
    ledger.begin("req_test", principal, "fake-model", "fake", "openai")
    stream = MeteredBackend(Slow(), ledger, writer).stream(
        Generate("fake-model", (Message("user", (Text("x"),)),), request_id="req_test")
    )
    task = asyncio.create_task(anext(stream))
    await task
    reader = asyncio.create_task(anext(stream))
    await asyncio.sleep(0.05)
    reader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reader
    await stream.aclose()
    await writer.flush()
    writer.close()
    assert requests(ledger) == [("disconnected", "pending")]
