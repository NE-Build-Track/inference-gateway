import json

import httpx
import pytest
from livepeer_builder.testing import FakeEngine, Reply, runner
from openai import AsyncOpenAI

from inference_gateway.adapters.response_api import stream
from inference_gateway.adapters.responses import ResponseIdentity
from inference_gateway.app import Settings, create_app
from inference_gateway.backends.livepeer import LivepeerBackend
from inference_gateway.backends.native_responses import ResponsesDecoder
from inference_gateway.config import LivepeerSettings, ModelRoute
from inference_gateway.core.backend import UnsupportedResponse
from inference_gateway.core.models import BlockEnd, BlockStart, Delta, Finished, ToolCall
from inference_gateway.testing import FakeBackend


async def native_frames(tool=False):
    async def events():
        yield BlockStart(0, call=ToolCall("call_1", "weather", "") if tool else None)
        yield Delta(0, "{}" if tool else "Hello")
        yield BlockEnd(0)
        yield Finished("tool_calls" if tool else "stop", 12, 3, 2)

    return [
        frame async for frame in stream(events(), ResponseIdentity("resp_runner", "gemma", 123))
    ]


def backend(frames):
    engine = FakeEngine(
        streaming=True,
        runners=[runner("chat", metadata='{"model_name":"gemma"}')],
        replies={
            ("chat", "/v1/responses"): Reply(
                content_type="text/event-stream", chunks=tuple(f.encode() for f in frames)
            )
        },
    )
    route = ModelRoute(
        name="gemma", app="chat", upstream_api="responses", capabilities={"parallel_tools"}
    )
    return LivepeerBackend(engine, LivepeerSettings(routes=(route,))), engine


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("native", [False, True])
async def test_responses_sdk_text_and_usage(native, streaming):
    selected, engine = backend(await native_frames()) if native else (FakeBackend(), None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(selected))) as http:
        client = AsyncOpenAI(
            api_key="test", base_url="http://test/v1", http_client=http, max_retries=0
        )
        result = await client.responses.create(
            model="gemma" if native else "fake-model", input="hello", store=False, stream=streaming
        )
        if streaming:
            events = [event async for event in result]
            assert [e.sequence_number for e in events] == list(range(len(events)))
            assert events[0].type == "response.created"
            assert events[-1].type == "response.completed"
            result = events[-1].response
            assert (
                "".join(e.delta for e in events if e.type == "response.output_text.delta")
                == result.output_text
            )
        assert result.status == "completed"
        assert result.output_text == ("Hello" if native else "Echo: hello")
        assert result.usage.total_tokens == result.usage.input_tokens + result.usage.output_tokens
        if native:
            assert result.usage.input_tokens_details.cached_tokens == 2
            sent = engine.jobs.requests[0][1]
            assert sent.path == "/v1/responses" and sent.model == "gemma"
            assert sent.payload["model"] == "gemma" and sent.payload["store"] is False


@pytest.mark.parametrize("streaming", [False, True])
async def test_native_function_call_and_stateless_result(streaming):
    selected, engine = backend(await native_frames(True))
    app = create_app(selected)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
        client = AsyncOpenAI(
            api_key="test", base_url="http://test/v1", http_client=http, max_retries=0
        )
        result = await client.responses.create(
            model="gemma",
            input="weather?",
            store=False,
            tools=[
                {
                    "type": "function",
                    "name": "weather",
                    "parameters": {"type": "object"},
                    "strict": False,
                }
            ],
            stream=streaming,
        )
        if streaming:
            events = [e async for e in result]
            result = events[-1].response
        call = result.output[0]
        assert (
            call.type == "function_call"
            and call.call_id == "call_1"
            and json.loads(call.arguments) == {}
        )
        await client.responses.create(
            model="gemma",
            store=False,
            input=[
                {"role": "user", "content": "weather?"},
                call.model_dump(exclude_none=True),
                {"type": "function_call_output", "call_id": call.call_id, "output": "sunny"},
            ],
        )
        sent = engine.jobs.requests[-1][1].payload
        assert sent["input"][-1] == {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "sunny",
        }


@pytest.mark.parametrize(
    "extra",
    [
        {"store": True},
        {"previous_response_id": "resp_old"},
        {"background": True},
        {"unknown": True},
        {"input": [{"role": "user"}]},
        {"input": [{"role": "user", "content": [{"type": "input_image"}]}]},
        {"tools": [{"type": "web_search"}]},
        {"tools": [{"type": "function", "name": "f", "strict": True}]},
    ],
)
async def test_unsupported_responses_fail_before_dispatch(extra):
    selected, engine = backend(await native_frames())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(selected)), base_url="http://test"
    ) as http:
        result = await http.post("/v1/responses", json={"model": "gemma", "input": "hi", **extra})
    assert result.status_code == 400 and not engine.jobs.requests


async def test_catalog_auth_and_no_paid_dispatch():
    selected, engine = backend(await native_frames())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(selected, Settings(api_key="secret"))),
        base_url="http://test",
    ) as http:
        assert (await http.get("/v1/models")).status_code == 401
        response = await http.get("/v1/models", headers={"Authorization": "Bearer secret"})
        assert response.json()["data"] == [
            {"id": "gemma", "object": "model", "created": 0, "owned_by": "gateway"}
        ]
    assert not engine.jobs.requests


@pytest.mark.parametrize(
    "mutation", ["usage", "identity", "sequence", "text", "missing_end", "unknown", "status"]
)
async def test_native_response_corruption_rejected(mutation):
    frames = await native_frames()
    values = [json.loads(f.split("data: ", 1)[1]) for f in frames]
    if mutation == "usage":
        values[-1]["response"]["usage"]["total_tokens"] = 999
    elif mutation == "identity":
        values[-1]["response"]["id"] = "other"
    elif mutation == "sequence":
        values[-1]["sequence_number"] = 0
    elif mutation == "text":
        values[-1]["response"]["output"][0]["content"][0]["text"] = "changed"
    elif mutation == "status":
        for value in values:
            if value["type"] == "response.output_item.done":
                value["item"]["status"] = "in_progress"
    elif mutation == "missing_end":
        values = [v for v in values if v["type"] != "response.output_item.done"]
    else:
        values[2]["item"]["type"] = "web_search_call"
    decoder = ResponsesDecoder(10000)
    with pytest.raises(UnsupportedResponse):
        for value in values:
            decoder.feed(json.dumps(value))


@pytest.mark.parametrize("streaming", [False, True])
async def test_responses_registry_usage_and_job_correlation(tmp_path, streaming):
    from decimal import Decimal

    from inference_gateway.adapters.ledger import SQLiteLedger
    from inference_gateway.core.accounting import Price

    ledger = SQLiteLedger(str(tmp_path / "ledger.db"))
    uid = ledger.create_user("test")
    _, key = ledger.create_key(uid, "test", frozenset({"inference", "reports"}))
    ledger.set_price(Price("v1", "gemma", "usd", Decimal("1"), Decimal("2")))
    selected, engine = backend(await native_frames())
    selected._usage_recorder = ledger.record_usage
    selected._job_recorder = ledger.bind_job
    app = create_app(selected, Settings(backend="livepeer", accounting_db=ledger.path))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        headers = {"Authorization": "Bearer " + key}
        catalog = await http.get("/v1/models", headers=headers)
        assert [m["id"] for m in catalog.json()["data"]] == ["gemma"]
        result = await http.post(
            "/v1/responses",
            headers=headers,
            json={"model": "gemma", "input": "hello", "stream": streaming},
        )
        assert result.status_code == 200
        with ledger.connection() as db:
            row = db.execute(
                "SELECT * FROM requests WHERE id=?", (result.headers["x-request-id"],)
            ).fetchone()
            assert (
                row["input_tokens"] == 12
                and row["output_tokens"] == 3
                and row["cached_input_tokens"] == 2
            )
            assert row["job_id"] and row["outcome"] == "succeeded"
            assert db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1
        assert engine.jobs.requests[0][1].operation_ref == result.headers["x-request-id"]
        assert len(engine.jobs.requests) == 1


async def test_models_excludes_unpriced_and_reports_only_key(tmp_path):
    from inference_gateway.adapters.ledger import SQLiteLedger

    ledger = SQLiteLedger(str(tmp_path / "ledger.db"))
    uid = ledger.create_user("test")
    _, key = ledger.create_key(uid, "test", frozenset({"inference"}))
    _, reports = ledger.create_key(uid, "reports", frozenset({"reports"}))
    selected, engine = backend(await native_frames())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=create_app(selected, Settings(accounting_db=ledger.path))
        ),
        base_url="http://test",
    ) as http:
        result = await http.get("/v1/models", headers={"Authorization": "Bearer " + key})
        assert result.json()["data"] == []
        assert (
            await http.get("/v1/models", headers={"Authorization": "Bearer " + reports})
        ).status_code == 403
    assert not engine.jobs.requests


async def test_native_stream_failure_has_no_success_terminal():
    frames = await native_frames()
    # A valid partial answer followed by invalid terminal usage must fail visibly.
    last = json.loads(frames[-1].split("data: ", 1)[1])
    del last["response"]["usage"]
    frames[-1] = "data: " + json.dumps(last) + "\n\n"
    selected, _ = backend(frames)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(selected)), base_url="http://test"
    ) as http:
        result = await http.post(
            "/v1/responses", json={"model": "gemma", "input": "hi", "stream": True}
        )
        assert result.status_code == 200
        assert "response.completed" not in result.text
        assert "event: error" in result.text and "Unsupported Response" in result.text


async def test_responses_image_input_forwarding():
    from inference_gateway.testing import PIXEL

    selected, engine = backend(await native_frames())
    selected._routes = (
        ModelRoute(
            name="gemma",
            app="chat",
            upstream_api="responses",
            capabilities={"media_input"},
            input_media={"image"},
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(selected)), base_url="http://test"
    ) as http:
        result = await http.post(
            "/v1/responses",
            json={
                "model": "gemma",
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "color?"},
                            {"type": "input_image", "image_url": "data:image/png;base64," + PIXEL},
                        ],
                    }
                ],
            },
        )
        assert result.status_code == 200
    assert engine.jobs.requests[0][1].payload["input"][0]["content"][1]["image_url"].endswith(PIXEL)


@pytest.mark.parametrize("deadline", [False, True])
async def test_native_cancellation_and_deadline_close_engine_stream(monkeypatch, deadline):
    import asyncio

    from livepeer_builder.testing import FakeJobStream

    from inference_gateway.app import deadline_stream
    from inference_gateway.core.models import Generate, Message, Text

    recorded = []
    original = FakeJobStream.disconnected

    async def disconnected(self):
        recorded.append(self)
        await original(self)

    monkeypatch.setattr(FakeJobStream, "disconnected", disconnected)
    selected, _ = backend(await native_frames())
    events = selected.stream(Generate("gemma", (Message("user", (Text("hi"),)),)))
    if deadline:
        original_iterator = events

        async def slow():
            try:
                yield await anext(original_iterator)
                await asyncio.sleep(10)
            finally:
                await original_iterator.aclose()

        events = deadline_stream(slow(), asyncio.get_running_loop().time() + 0.1)
    await anext(events)
    if deadline:
        with pytest.raises(TimeoutError):
            await anext(events)
    await events.aclose()
    assert len(recorded) == 1 and recorded[0].closed and recorded[0].disconnected_called


async def test_sdk_responses_stream_accumulator():
    selected, _ = backend(await native_frames())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(selected))) as http:
        client = AsyncOpenAI(
            api_key="test", base_url="http://test/v1", http_client=http, max_retries=0
        )
        async with client.responses.stream(model="gemma", input="hello", store=False) as events:
            result = await events.get_final_response()
        assert result.output_text == "Hello" and result.usage.total_tokens == 15


async def test_responses_truncation_is_incomplete():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as http:
        for streaming in (False, True):
            result = await http.post(
                "/v1/responses",
                json={
                    "model": "fake-model",
                    "input": "hello",
                    "max_output_tokens": 2,
                    "stream": streaming,
                },
            )
            assert result.status_code == 200
            if streaming:
                values = [
                    json.loads(line[6:])
                    for line in result.text.splitlines()
                    if line.startswith("data: ")
                ]
                assert values[-1]["type"] == "response.incomplete"
                value = values[-1]["response"]
            else:
                value = result.json()
            assert value["status"] == "incomplete" and value["incomplete_details"] == {
                "reason": "max_output_tokens"
            }


async def test_responses_structured_text():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as http:
        result = await http.post(
            "/v1/responses",
            json={
                "model": "fake-model",
                "input": "json please",
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "answer",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {"answer": {"type": "string", "enum": ["blue"]}},
                            "required": ["answer"],
                            "additionalProperties": False,
                        },
                    }
                },
            },
        )
        assert result.status_code == 200
        assert json.loads(result.json()["output"][0]["content"][0]["text"]) == {"answer": "blue"}


async def test_native_limit_includes_item_identity():
    values = [json.loads(frame.split("data: ", 1)[1]) for frame in await native_frames()]
    values[2]["item"]["id"] = "x" * 1000
    decoder = ResponsesDecoder(100)
    with pytest.raises(UnsupportedResponse):
        for value in values:
            decoder.feed(json.dumps(value))
