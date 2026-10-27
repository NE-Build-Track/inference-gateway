import json

import httpx
import pytest

from inference_gateway.app import create_app
from inference_gateway.testing import FakeBackend

SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}, "units": {"type": ["string", "null"]}},
    "required": ["city", "units"],
    "additionalProperties": False,
}
FORMAT = {
    "type": "json_schema",
    "name": "answer",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    },
}


def body(strict=True):
    return {
        "model": "fake-model",
        "input": "weather",
        "tools": [{"type": "function", "name": "weather", "parameters": SCHEMA, "strict": strict}],
        "tool_choice": "required",
    }


@pytest.mark.parametrize("streaming", [False, True])
async def test_strict_tools_and_structured_text_are_accepted(streaming):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as http:
        response = await http.post(
            "/v1/responses", json={**body(), "text": {"format": FORMAT}, "stream": streaming}
        )
    assert response.status_code == 200
    result = (
        response.json()
        if not streaming
        else [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ][-1]["response"]
    )
    args = json.loads(result["output"][0]["arguments"])
    assert set(args) == {"city", "units"} and isinstance(args["city"], str)


@pytest.mark.parametrize("streaming", [False, True])
async def test_strict_invalid_arguments_never_complete(streaming):
    fake = FakeBackend(tool_arguments={"weather": {"city": 123, "units": None}})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(fake)), base_url="http://test"
    ) as http:
        response = await http.post("/v1/responses", json={**body(), "stream": streaming})
    assert response.status_code == (200 if streaming else 502)
    assert "unsupported_response" in response.text
    for terminal in (
        "response.completed",
        "response.function_call_arguments.done",
        "response.output_item.done",
    ):
        assert terminal not in response.text


async def scripted_backend(native, arguments, *, ledger=None, text=None):
    from livepeer_builder.testing import FakeEngine, Reply, runner
    from test_responses_api import native_frames

    from inference_gateway.backends.livepeer import LivepeerBackend
    from inference_gateway.config import LivepeerSettings, ModelRoute

    if native:
        frames = await native_frames(tool=text is None)

        def substitute(value):
            if isinstance(value, dict):
                return {k: substitute(v) for k, v in value.items()}
            if isinstance(value, list):
                return [substitute(v) for v in value]
            if value == "{}":
                return json.dumps(arguments)
            if value == "Hello" and text is not None:
                return text
            return value

        frames = [
            "data: " + json.dumps(substitute(json.loads(f.split("data: ", 1)[1]))) + "\n\n"
            for f in frames
        ]
    else:
        delta = (
            {"content": text}
            if text is not None
            else {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "weather", "arguments": json.dumps(arguments)},
                    }
                ]
            }
        )
        values = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop" if text is not None else "tool_calls",
                    }
                ]
            },
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 3,
                    "prompt_tokens_details": {"cached_tokens": 2},
                },
            },
        ]
        frames = ["data: " + json.dumps(v) + "\n\n" for v in values] + ["data: [DONE]\n\n"]
    path = "/v1/responses" if native else "/v1/chat/completions"
    engine = FakeEngine(
        streaming=True,
        runners=[runner("chat", metadata='{"model_name":"gemma"}')],
        replies={
            ("chat", path): Reply(
                content_type="text/event-stream", chunks=tuple(f.encode() for f in frames)
            )
        },
    )
    route = ModelRoute(
        name="gemma",
        app="chat",
        upstream_api="responses" if native else "chat_completions",
        capabilities={"structured_output", "parallel_tools"},
    )
    backend = LivepeerBackend(
        engine,
        LivepeerSettings(routes=(route,)),
        ledger.record_usage if ledger else None,
        ledger.bind_job if ledger else None,
    )
    return backend, engine


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("valid", [False, True])
async def test_strict_native_and_chat_usage_accounting(tmp_path, native, streaming, valid):
    from decimal import Decimal

    from inference_gateway.adapters.ledger import SQLiteLedger
    from inference_gateway.config import Settings
    from inference_gateway.core.accounting import Price

    ledger = SQLiteLedger(str(tmp_path / "retail.db"))
    uid = ledger.create_user("test")
    _, key = ledger.create_key(uid, "test", frozenset({"inference"}))
    ledger.set_price(Price("strict-v1", "gemma", "usd", Decimal("1"), Decimal("2")))
    arguments = {"city": "Paris" if valid else 123, "units": None}
    backend, engine = await scripted_backend(native, arguments, ledger=ledger)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=create_app(backend, Settings(backend="livepeer", accounting_db=ledger.path))
        ),
        base_url="http://test",
    ) as http:
        response = await http.post(
            "/v1/responses",
            headers={"Authorization": "Bearer " + key},
            json={**body(), "model": "gemma", "text": {"format": FORMAT}, "stream": streaming},
        )
    assert response.status_code == (200 if valid or streaming else 502)
    if valid:
        result = (
            response.json()
            if not streaming
            else [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ][-1]["response"]
        )
        assert json.loads(result["output"][0]["arguments"]) == arguments
        assert result["usage"]["input_tokens"] == 12 and result["usage"]["output_tokens"] == 3
    else:
        assert "unsupported_response" in response.text and "response.completed" not in response.text
        assert "response.function_call_arguments.done" not in response.text
        assert "response.output_item.done" not in response.text
    sent = engine.jobs.requests[0][1].payload
    function = sent["tools"][0] if native else sent["tools"][0]["function"]
    assert function["strict"] is True and function["parameters"] == SCHEMA
    assert "text" in sent if native else "response_format" in sent
    with ledger.connection() as db:
        row = db.execute("SELECT * FROM requests").fetchone()
        assert (
            row["input_tokens"] == 12
            and row["output_tokens"] == 3
            and row["cached_input_tokens"] == 2
        )
        assert row["outcome"] == ("succeeded" if valid else "failed") and row["job_id"]
        assert db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] == 1
    assert len(engine.jobs.requests) == 1


@pytest.mark.parametrize("strict", [False, None])
async def test_false_and_omitted_strict_do_not_enforce_schema(strict):
    fake = FakeBackend(tool_arguments={"weather": {"unrelated": 42}})
    request = body(strict)
    if strict is None:
        request["tools"][0].pop("strict")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(fake)), base_url="http://test"
    ) as http:
        response = await http.post("/v1/responses", json=request)
    assert response.status_code == 200
    assert json.loads(response.json()["output"][0]["arguments"]) == {"unrelated": 42}


@pytest.mark.parametrize(
    "arguments",
    [{}, {"city": "Paris", "units": None, "extra": 1}, {"city": "Paris", "units": True}],
)
async def test_strict_missing_extra_and_wrong_type_rejected(arguments):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=create_app(FakeBackend(tool_arguments={"weather": arguments}))
        ),
        base_url="http://test",
    ) as http:
        response = await http.post("/v1/responses", json=body())
    assert response.status_code == 502 and "unsupported_response" in response.text


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("valid", [False, True])
async def test_final_text_schema_is_checked_with_tools_present(native, valid):
    backend, _ = await scripted_backend(
        native, {}, text=json.dumps({"answer": "blue"} if valid else {"answer": 9})
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(backend)), base_url="http://test"
    ) as http:
        result = await http.post(
            "/v1/responses",
            json={**body(), "model": "gemma", "tool_choice": "none", "text": {"format": FORMAT}},
        )
    assert result.status_code == (200 if valid else 502)


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object"},
        {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": [],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"x": {"type": "object"}},
            "required": ["x"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
            "unsupported": True,
        },
    ],
)
async def test_invalid_strict_schema_rejected_before_dispatch(schema):
    backend, engine = await scripted_backend(True, {})
    request = body()
    request["model"] = "gemma"
    request["tools"][0]["parameters"] = schema
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(backend)), base_url="http://test"
    ) as http:
        response = await http.post("/v1/responses", json=request)
    assert response.status_code == 400 and not engine.jobs.requests


@pytest.mark.parametrize("native", [False, True])
async def test_chat_completions_strict_flag_is_preserved(native):
    backend, engine = await scripted_backend(native, {"city": "Paris", "units": None})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(backend)), base_url="http://test"
    ) as http:
        result = await http.post(
            "/v1/chat/completions",
            json={
                "model": "gemma",
                "messages": [{"role": "user", "content": "weather"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": "weather", "strict": True, "parameters": SCHEMA},
                    }
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {k: v for k, v in FORMAT.items() if k != "type"},
                },
            },
        )
    assert result.status_code == 200
    assert json.loads(
        result.json()["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    ) == {"city": "Paris", "units": None}
    sent = engine.jobs.requests[0][1].payload
    assert (sent["tools"][0] if native else sent["tools"][0]["function"])["strict"] is True


@pytest.mark.parametrize("valid", [False, True])
async def test_nested_arrays_enums_and_nullable_anyof(valid):
    schema = {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer"},
                        "label": {
                            "anyOf": [{"type": "string", "enum": ["blue"]}, {"type": "null"}]
                        },
                    },
                    "required": ["count", "label"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["rows"],
        "additionalProperties": False,
    }
    args = {"rows": [{"count": 2, "label": None}, {"count": 3 if valid else True, "label": "blue"}]}
    request = body()
    request["tools"][0]["parameters"] = schema
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=create_app(FakeBackend(tool_arguments={"weather": args}))
        ),
        base_url="http://test",
    ) as http:
        result = await http.post("/v1/responses", json=request)
    assert result.status_code == (200 if valid else 502)


async def test_fake_tool_followup_can_produce_structured_final_answer():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as http:
        first = await http.post("/v1/responses", json={**body(), "text": {"format": FORMAT}})
        call = first.json()["output"][0]
        result = await http.post(
            "/v1/responses",
            json={
                **body(),
                "tool_choice": "auto",
                "text": {"format": FORMAT},
                "input": [
                    {"role": "user", "content": "weather"},
                    call,
                    {"type": "function_call_output", "call_id": call["call_id"], "output": "sunny"},
                ],
            },
        )
    assert result.status_code == 200
    assert isinstance(json.loads(result.json()["output"][0]["content"][0]["text"])["answer"], str)


@pytest.mark.parametrize("native", [False, True])
async def test_sdk_strict_stream_accumulates_function_call(native):
    from openai import AsyncOpenAI

    backend, _ = await scripted_backend(native, {"city": "Paris", "units": None})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(backend))) as http:
        client = AsyncOpenAI(
            api_key="test", base_url="http://test/v1", http_client=http, max_retries=0
        )
        request = body()
        request["model"] = "gemma"
        async with client.responses.stream(**request, text={"format": FORMAT}) as stream:
            result = await stream.get_final_response()
    assert result.status == "completed"
    assert json.loads(result.output[0].arguments) == {"city": "Paris", "units": None}


async def test_mixed_parallel_tools_only_enforce_explicit_strict():
    request = body()
    request["parallel_tool_calls"] = True
    request["tools"].append(
        {"type": "function", "name": "loose", "strict": False, "parameters": SCHEMA}
    )
    fake = FakeBackend(
        tool_arguments={"weather": {"city": "Paris", "units": None}, "loose": {"anything": True}}
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(fake)), base_url="http://test"
    ) as http:
        result = await http.post("/v1/responses", json=request)
    assert result.status_code == 200
    assert {part["name"]: json.loads(part["arguments"]) for part in result.json()["output"]} == {
        "weather": {"city": "Paris", "units": None},
        "loose": {"anything": True},
    }


async def test_cancellation_while_draining_invalid_tool_closes_backend():
    import asyncio

    from inference_gateway.adapters.response_api import ResponsesRequest, command
    from inference_gateway.core.models import BlockEnd, BlockStart, Delta, ToolCall
    from inference_gateway.core.service import GenerationService

    class Pausing:
        closed = False

        async def stream(self, request):
            try:
                yield BlockStart(0, call=ToolCall("one", "weather", ""))
                yield Delta(0, "{}")
                yield BlockEnd(0)
                await asyncio.sleep(10)
            finally:
                self.closed = True

    backend = Pausing()
    stream = GenerationService(backend).stream(command(ResponsesRequest.model_validate(body())))
    await anext(stream)
    await anext(stream)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(anext(stream), 0.05)
    await stream.aclose()
    assert backend.closed
