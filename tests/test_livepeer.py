import json
from dataclasses import replace

import httpx
import pytest
from anthropic import APIStatusError as AnthropicStatusError
from anthropic import AsyncAnthropic
from livepeer_builder import ActorContext, EngineSupply, RunnerCallError
from livepeer_builder.testing import (
    Fails,
    FakeEngine,
    FakeJobStream,
    Reply,
    ScriptedStreamTransport,
    StreamScript,
    make_engine,
    runner,
)
from openai import APIStatusError as OpenAIStatusError
from openai import AsyncOpenAI

from endpoint_gateway.app import create_app
from endpoint_gateway.backends.livepeer import LivepeerBackend
from endpoint_gateway.config import LivepeerSettings, ModelRoute
from endpoint_gateway.core.backend import BackendDeadline, BackendError, UnknownModel
from endpoint_gateway.core.models import (
    BlockStart,
    Generate,
    Message,
    NamedTool,
    Text,
    Tool,
    ToolCall,
    ToolResult,
)
from endpoint_gateway.core.service import GenerationService

APP = "blueclaw/chat/qwen3.8-27b"
PATH = "/v1/chat/completions"
OPTIONS = LivepeerSettings(routes=(ModelRoute(name="qwen", app=APP),))
ACTOR = ActorContext(
    actor_id=OPTIONS.actor_id,
    application_id=OPTIONS.application_id,
    permissions=frozenset({"jobs.run", "jobs.read", "costs.read"}),
)
REQUEST = Generate("qwen", (Message("user", (Text("hello"),)),), request_id="req_test")


def frame(delta=None, finish=None, usage=None):
    payload = {
        "choices": [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]
    }
    if usage is not None:
        payload["usage"] = usage
    return ("data: " + json.dumps(payload, ensure_ascii=False) + "\r\n\r\n").encode()


def ending(reason="stop"):
    return (
        frame({}, reason),
        frame(usage={"prompt_tokens": 7, "completion_tokens": 3, "cached_prompt_tokens": 0}),
        b"data: [DONE]\r\n\r\n",
    )


def scripted(chunks):
    return FakeEngine(
        streaming=True,
        runners=[runner(APP)],
        replies={(APP, PATH): Reply(content_type="text/event-stream", chunks=tuple(chunks))},
    )


def tool_frames():
    return (
        frame(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_a",
                        "type": "function",
                        "function": {"name": "weather", "arguments": '{"city":'},
                    }
                ]
            }
        ),
        frame({"tool_calls": [{"index": 0, "function": {"arguments": '"Boston"}'}}]}),
        *ending("tool_calls"),
    )


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_livepeer_sdk_text(protocol, streaming):
    wire = frame({"content": "Héllo"}) + b"".join(ending())
    # Fragment at every byte, including inside the Unicode code point.
    engine = scripted([wire[i : i + 1] for i in range(len(wire))])
    app = create_app(LivepeerBackend(engine, OPTIONS))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
        if protocol == "openai":
            client = AsyncOpenAI(base_url="http://test/v1", api_key="local", http_client=http)
            result = await client.chat.completions.create(
                model="qwen", messages=[{"role": "user", "content": "hi"}], stream=streaming
            )
            text = (
                "".join([c.choices[0].delta.content or "" async for c in result])
                if streaming
                else result.choices[0].message.content
            )
        else:
            client = AsyncAnthropic(base_url="http://test", api_key="local", http_client=http)
            args = {
                "model": "qwen",
                "max_tokens": 50,
                "messages": [{"role": "user", "content": "hi"}],
            }
            if streaming:
                async with client.messages.stream(**args) as stream:
                    text = "".join([p async for p in stream.text_stream])
            else:
                text = (await client.messages.create(**args)).content[0].text
    assert text == "Héllo"
    actor, sent = engine.jobs.requests[0]
    assert actor.actor_id == OPTIONS.actor_id and actor.application_id == OPTIONS.application_id
    assert sent.app == APP and sent.operation_ref.startswith("req_")
    assert sent.payload["stream_options"] == {"include_usage": True}
    assert sent.payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert engine.jobs.usage_reports[0][1] == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "cached_prompt_tokens": 0,
    }
    job = (await engine.jobs.list_jobs(actor))[0]
    assert job.state == "succeeded"


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_livepeer_sdk_tool_roundtrip(protocol):
    engine = scripted(tool_frames())
    app = create_app(LivepeerBackend(engine, OPTIONS))
    messages = [{"role": "user", "content": "weather?"}]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
        if protocol == "openai":
            client = AsyncOpenAI(base_url="http://test/v1", api_key="local", http_client=http)
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "weather",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                        },
                    },
                }
            ]
            stream = await client.chat.completions.create(
                model="qwen", messages=messages, tools=tools, stream=True
            )
            chunks = [c async for c in stream]
            calls = [t for c in chunks for t in (c.choices[0].delta.tool_calls or [])]
            assert json.loads("".join(t.function.arguments or "" for t in calls)) == {
                "city": "Boston"
            }
            messages += [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": calls[0].id,
                            "type": "function",
                            "function": {"name": "weather", "arguments": '{"city":"Boston"}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": calls[0].id, "content": "sunny"},
            ]
        else:
            client = AsyncAnthropic(base_url="http://test", api_key="local", http_client=http)
            tools = [{"name": "weather", "input_schema": {"type": "object"}}]
            async with client.messages.stream(
                model="qwen", messages=messages, tools=tools, max_tokens=100
            ) as stream:
                final = await stream.get_final_message()
            assert final.content[0].input == {"city": "Boston"}
            messages += [
                {"role": "assistant", "content": [final.content[0].model_dump()]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": final.content[0].id,
                            "content": "sunny",
                        }
                    ],
                },
            ]
        engine.jobs.replies[(APP, PATH)] = Reply(
            content_type="text/event-stream", chunks=(frame({"content": "It is sunny"}), *ending())
        )
        if protocol == "openai":
            result = await client.chat.completions.create(
                model="qwen", messages=messages, tools=tools
            )
            assert result.choices[0].message.content == "It is sunny"
        else:
            result = await client.messages.create(
                model="qwen", messages=messages, tools=tools, max_tokens=100
            )
            assert result.content[0].text == "It is sunny"
    sent = engine.jobs.requests[-1][1].payload
    assert sent["messages"][-1] == {"role": "tool", "tool_call_id": "call_a", "content": "sunny"}
    assert engine.jobs.requests[0][1].operation_ref != engine.jobs.requests[1][1].operation_ref


async def test_unknown_model_never_dispatches():
    engine = scripted([])
    with pytest.raises(UnknownModel):
        await GenerationService(LivepeerBackend(engine, OPTIONS)).complete(
            replace(REQUEST, model="unknown")
        )
    assert engine.jobs.requests == []


async def test_named_tool_and_upstream_model_are_preserved():
    options = LivepeerSettings(
        routes=(
            ModelRoute(name="qwen", app=APP, upstream_model="served-model", disable_thinking=False),
        )
    )
    engine = scripted(tool_frames())
    request = replace(
        REQUEST,
        tools=(Tool("weather", "Forecast", {"type": "object"}),),
        tool_choice=NamedTool("weather"),
        temperature=0.2,
    )
    await GenerationService(LivepeerBackend(engine, options)).complete(request)
    sent = engine.jobs.requests[0][1].payload
    assert sent["model"] == "served-model"
    assert sent["tool_choice"] == {"type": "function", "function": {"name": "weather"}}
    assert sent["temperature"] == 0.2
    assert "chat_template_kwargs" not in sent


async def test_tool_error_and_mixed_user_content_are_not_lost():
    engine = scripted([frame({"content": "failed"}), *ending()])
    request = replace(
        REQUEST,
        messages=(
            Message("assistant", (ToolCall("c", "weather", "{}"),)),
            Message("user", (ToolResult("c", "unavailable", True), Text("Try later"))),
        ),
    )
    await GenerationService(LivepeerBackend(engine, OPTIONS)).complete(request)
    sent = engine.jobs.requests[0][1].payload["messages"]
    assert sent[-2] == {"role": "tool", "tool_call_id": "c", "content": "Tool error: unavailable"}
    assert sent[-1] == {"role": "user", "content": "Try later"}


@pytest.mark.parametrize(
    "chunks",
    [
        [frame({"content": "partial"})],
        [b"data: not-json\n\n"],
        [b'data: {"error":{"message":"private"}}\n\n'],
        [frame({"content": "x"}), frame({}, "stop"), b"data: [DONE]\n\n"],
        [frame({"content": "x"}), *ending(), frame({"content": "late"})],
        [b"data: \xff\n\n"],
        [frame({"reasoning_content": "unsupported"}), *ending()],
    ],
)
async def test_bad_runner_stream_fails_without_success(chunks):
    # One raw chunk ensures the parser sees trailing data after DONE in the same read.
    engine = scripted([b"".join(chunks)])
    with pytest.raises(BackendError):
        await GenerationService(LivepeerBackend(engine, OPTIONS)).complete(REQUEST)
    assert len(engine.jobs.requests) == 1


async def test_usage_report_failure_preserves_completed_reply(caplog):
    engine = scripted([frame({"content": "ok"}), *ending()])

    async def fail(*args):
        raise RuntimeError("private secret detail")

    engine.jobs.report_usage = fail
    result = await GenerationService(LivepeerBackend(engine, OPTIONS)).complete(REQUEST)
    assert result.content == (Text("ok"),)
    assert "livepeer_usage_report_failed" in caplog.text
    assert "private secret" not in caplog.text


async def test_close_calls_engine_disconnect_before_iterator_close(monkeypatch):
    streams = []
    original = FakeJobStream.disconnected

    async def record(self):
        streams.append(self)
        await original(self)

    monkeypatch.setattr(FakeJobStream, "disconnected", record)
    engine = scripted([frame({"content": "first"}), *ending()])
    events = LivepeerBackend(engine, OPTIONS).stream(REQUEST)
    assert isinstance(await anext(events), BlockStart)
    await events.aclose()
    assert len(streams) == 1 and streams[0].disconnected_called
    # Fake settles remaining data immediately. Preserve its success instead of overwriting it.
    assert streams[0].job.state == "succeeded"
    assert streams[0].closed


@pytest.mark.parametrize(
    "failure",
    [
        Fails(kind="timeout", payment_sent=True),
        Fails(kind="runner_error", payment_sent=True),
    ],
)
async def test_paid_failure_does_not_retry(failure):
    engine = scripted([frame({"content": "partial"}), failure])
    with pytest.raises(BackendError):
        await GenerationService(LivepeerBackend(engine, OPTIONS)).complete(REQUEST)
    assert len(engine.jobs.requests) == 1
    assert (await engine.jobs.list_jobs(ACTOR))[0].state == "uncertain"


@pytest.mark.parametrize("disconnect", [False, True])
async def test_real_engine_records_completion_or_abandonment(disconnect):
    events = (
        [frame({"content": "first"}), 10.0]
        if disconnect
        else [frame({"content": "done"}), *ending()]
    )
    transport = ScriptedStreamTransport(opens=[StreamScript(events=events)])
    async with make_engine(
        runners=[runner(APP)],
        supply=EngineSupply(transport=transport),
        allocation_mode="per_application",
        shared_payment_key="fixture-key",
        shared_allocation_id="fixture-allocation",
        stream_settle_s=0.01,
    ) as engine:
        backend = LivepeerBackend(engine, OPTIONS)
        if disconnect:
            stream = backend.stream(REQUEST)
            await anext(stream)
            await stream.aclose()
        else:
            result = await GenerationService(backend).complete(REQUEST)
            assert result.content == (Text("done"),)
        job = (await engine.jobs.list_jobs(ACTOR))[0]
        assert job.state == ("uncertain" if disconnect else "succeeded")
        assert job.attempts[-1].outcome == ("abandoned" if disconnect else "succeeded")
        assert job.attempts[-1].allocation_id == "fixture-allocation"
        if not disconnect:
            assert job.usage.quantities == {
                "prompt_tokens": 7,
                "completion_tokens": 3,
                "cached_prompt_tokens": 0,
            }
        assert transport.streams[0].closed
        assert engine.health().running_jobs == 0
        assert len(transport.calls) == 1


async def test_real_engine_timeout_preserves_paid_uncertainty():
    transport = ScriptedStreamTransport(opens=[StreamScript(events=[10.0])])
    async with make_engine(
        runners=[runner(APP)],
        supply=EngineSupply(transport=transport),
        allocation_mode="per_application",
        shared_payment_key="fixture-key",
        shared_allocation_id="fixture-allocation",
    ) as engine:
        options = OPTIONS.model_copy(
            update={"idle_timeout_seconds": 0.05, "first_byte_timeout_seconds": 0.05}
        )
        with pytest.raises(BackendDeadline):
            await GenerationService(LivepeerBackend(engine, options)).complete(REQUEST)
        job = (await engine.jobs.list_jobs(ACTOR))[0]
        assert job.state == "uncertain" and job.failure.kind == "timeout"
        assert len(transport.calls) == 1 and transport.streams[0].closed


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize(
    "kind,status", [("timeout", 504), ("no_capacity", 503), ("runner_error", 502)]
)
async def test_engine_failures_map_to_safe_http_errors(protocol, kind, status):
    engine = FakeEngine(
        streaming=True,
        runners=[runner(APP)],
        replies={
            (APP, PATH): Fails(kind=kind, message="upstream private secret", payment_sent=True)
        },
    )
    app = create_app(LivepeerBackend(engine, OPTIONS))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        path = "/v1/chat/completions" if protocol == "openai" else "/v1/messages"
        body = {"model": "qwen", "messages": [{"role": "user", "content": "hello"}], "stream": True}
        if protocol == "anthropic":
            body["max_tokens"] = 100
        response = await c.post(path, json=body, headers={"anthropic-version": "2023-06-01"})
    assert response.status_code == status
    assert "upstream private secret" not in response.text
    assert response.json()["error"]["type"]
    assert len(engine.jobs.requests) == 1


async def test_no_capacity_failover_is_owned_by_engine_only():
    transport = ScriptedStreamTransport(
        opens=[
            RunnerCallError("refused", "fixture refused", payment_sent=False),
            StreamScript(events=[frame({"content": "second runner"}), *ending()]),
        ]
    )
    async with make_engine(
        runners=[runner(APP, url="https://runner-a"), runner(APP, url="https://runner-b")],
        supply=EngineSupply(transport=transport),
        allocation_mode="per_application",
        shared_payment_key="fixture-key",
        shared_allocation_id="fixture-allocation",
    ) as engine:
        result = await GenerationService(LivepeerBackend(engine, OPTIONS)).complete(REQUEST)
        assert result.content == (Text("second runner"),)
        jobs = await engine.jobs.list_jobs(ACTOR)
        assert len(jobs) == 1 and len(jobs[0].attempts) == 2
        assert not jobs[0].attempts[0].payment_sent
        assert jobs[0].attempts[1].payment_sent


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("kind", ["timeout", "runner_error"])
async def test_default_sdk_retries_do_not_repeat_paid_failure(protocol, kind):
    engine = FakeEngine(
        streaming=True,
        runners=[runner(APP)],
        replies={(APP, PATH): Fails(kind=kind, payment_sent=True)},
    )
    app = create_app(LivepeerBackend(engine, OPTIONS))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
        messages = [{"role": "user", "content": "hello"}]
        if protocol == "openai":
            client = AsyncOpenAI(base_url="http://test/v1", api_key="local", http_client=http)
            with pytest.raises(OpenAIStatusError) as caught:
                await client.chat.completions.create(model="qwen", messages=messages)
        else:
            client = AsyncAnthropic(base_url="http://test", api_key="local", http_client=http)
            with pytest.raises(AnthropicStatusError) as caught:
                await client.messages.create(model="qwen", max_tokens=50, messages=messages)
        assert caught.value.response.headers["x-should-retry"] == "false"
    assert len(engine.jobs.requests) == 1
