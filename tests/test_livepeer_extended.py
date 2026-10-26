"""Extended runner contracts through the adapter, HTTP boundary and official SDKs."""

import json
from dataclasses import replace

import httpx
import pytest
from anthropic import AsyncAnthropic
from livepeer_builder.testing import FakeEngine, FakeJobStream, Reply, runner
from openai import AsyncOpenAI
from pydantic import ValidationError

from inference_gateway.app import create_app
from inference_gateway.backends.livepeer import LivepeerBackend
from inference_gateway.backends.livepeer_request import runner_payload
from inference_gateway.backends.openai_events import CompletionDecoder
from inference_gateway.backends.stop_sequences import StopFilter
from inference_gateway.config import LivepeerSettings, ModelRoute
from inference_gateway.core.backend import BackendError, UnsupportedFeature
from inference_gateway.core.models import (
    BlockEnd,
    Delta,
    Finished,
    Generate,
    Message,
    OutputFormat,
    Reasoning,
    Sampling,
    Text,
)
from inference_gateway.core.service import GenerationService

CAPS = frozenset({"structured_output", "reasoning", "stop", "parallel_tools"})
ROUTE = ModelRoute(name="extended", app="test/chat", capabilities=CAPS)
OPTIONS = LivepeerSettings(routes=(ROUTE, ModelRoute(name="basic", app="test/chat")))
REQUEST = Generate("extended", (Message("user", (Text("hello"),)),))
SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string", "enum": ["Boston"]}},
    "required": ["city"],
    "additionalProperties": False,
}


def frame(delta=None, finish=None, usage=None, stop=None):
    return (
        "data: "
        + json.dumps(
            {
                "choices": []
                if delta is None
                else [{"index": 0, "delta": delta, "finish_reason": finish, "stop_reason": stop}],
                **({"usage": usage} if usage else {}),
            }
        )
        + "\n\n"
    ).encode()


def ending(reason="stop", stop=None):
    return [
        frame({}, reason, stop=stop),
        frame(usage={"prompt_tokens": 11, "completion_tokens": 7}),
        b"data: [DONE]\n\n",
    ]


def scripted(frames):
    return FakeEngine(
        streaming=True,
        runners=[runner(ROUTE.app, metadata='{"model_name":"extended"}')],
        replies={
            (ROUTE.app, "/v1/chat/completions"): Reply(
                content_type="text/event-stream", chunks=frames
            )
        },
    )


def tool(index, arguments, id=None, name=None):
    return {
        "tool_calls": [
            {
                "index": index,
                **({"id": id} if id else {}),
                "function": {"arguments": arguments, **({"name": name} if name else {})},
            }
        ]
    }


def fixtures(feature):
    if feature == "reasoning":
        return [
            frame({"reasoning_content": "Think "}),
            frame({"reasoning": "briefly"}),
            frame({"content": "Hello"}),
            *ending(),
        ]
    if feature == "structured_output":
        return [frame({"content": '{"city":'}), frame({"content": '"Boston"}'}), *ending()]
    if feature == "stop":
        return [frame({"content": "before EN"}), frame({"content": "D hidden"}), *ending()]
    return [
        frame(tool(3, '{"city":', "one", "a")),
        frame(tool(9, '{"city":', "two", "b")),
        frame(tool(3, '"Boston"}')),
        frame(tool(9, '"Paris"}')),
        *ending("tool_calls"),
    ]


def body(feature, protocol):
    base = {"model": "extended", "messages": [{"role": "user", "content": "hello"}]}
    if protocol == "anthropic":
        base["max_tokens"] = 1024
    if feature == "reasoning":
        return base, {"gateway": {"reasoning": True}}
    if feature == "structured_output":
        base.update(
            {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "city", "schema": SCHEMA},
                }
            }
            if protocol == "openai"
            else {"output_config": {"format": {"type": "json_schema", "schema": SCHEMA}}}
        )
    elif feature == "stop":
        base["stop" if protocol == "openai" else "stop_sequences"] = ["END"]
    elif protocol == "openai":
        base.update(
            tools=[{"type": "function", "function": {"name": n}} for n in ("a", "b")],
            parallel_tool_calls=True,
        )
    else:
        base.update(
            tools=[{"name": n, "input_schema": {"type": "object"}} for n in ("a", "b")],
            tool_choice={"type": "auto", "disable_parallel_tool_use": False},
        )
    return base, {}


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("feature", sorted(CAPS))
async def test_extended_sdk_contract(protocol, streaming, feature):
    raw = b"".join(fixtures(feature))
    # Arbitrary byte fragmentation includes reasoning, stop prefixes and tool arguments.
    engine = scripted(tuple(raw[i : i + 3] for i in range(0, len(raw), 3)))
    app = create_app(LivepeerBackend(engine, OPTIONS))
    options, extra = body(feature, protocol)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
        if protocol == "openai":
            client = AsyncOpenAI(
                api_key="local", base_url="http://test/v1", http_client=http, max_retries=0
            )
            if streaming:
                stream = await client.chat.completions.create(
                    **options, extra_body=extra, stream=True, stream_options={"include_usage": True}
                )
                chunks = [c async for c in stream]
                text = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
                thinking = "".join(
                    getattr(c.choices[0].delta, "reasoning_content", "")
                    for c in chunks
                    if c.choices
                )
                calls = {}
                for c in chunks:
                    for call in c.choices[0].delta.tool_calls or [] if c.choices else []:
                        calls[call.index] = calls.get(call.index, "") + (
                            call.function.arguments or ""
                        )
                arguments = [json.loads(value) for value in calls.values()]
                usage = chunks[-1].usage
            else:
                result = await client.chat.completions.create(**options, extra_body=extra)
                message = result.choices[0].message
                text = message.content or ""
                thinking = getattr(message, "reasoning_content", "")
                arguments = [json.loads(c.function.arguments) for c in message.tool_calls or []]
                usage = result.usage
            assert usage.prompt_tokens == 11 and usage.completion_tokens == 7
        else:
            client = AsyncAnthropic(
                api_key="local", base_url="http://test", http_client=http, max_retries=0
            )
            if streaming:
                async with client.messages.stream(**options, extra_body=extra) as stream:
                    result = await stream.get_final_message()
            else:
                result = await client.messages.create(**options, extra_body=extra)
            text = "".join(b.text for b in result.content if b.type == "text")
            thinking = "".join(b.thinking for b in result.content if b.type == "thinking")
            arguments = [b.input for b in result.content if b.type == "tool_use"]
            assert result.usage.input_tokens == 11 and result.usage.output_tokens == 7
            if feature == "reasoning":
                assert result.content[0].signature == ""
            if feature == "stop":
                assert result.stop_reason == "stop_sequence" and result.stop_sequence == "END"
    sent = engine.jobs.requests[0][1].payload
    assert len(engine.jobs.requests) == 1
    if feature == "reasoning":
        assert thinking == "Think briefly" and text == "Hello"
        assert sent["chat_template_kwargs"] == {"enable_thinking": True}
    elif feature == "structured_output":
        assert json.loads(text) == {"city": "Boston"}
        assert sent["response_format"]["json_schema"]["schema"] == SCHEMA
    elif feature == "stop":
        assert text == "before " and sent["stop"] == ["END"]
        assert sent["include_stop_str_in_output"] is True
    else:
        assert arguments == [{"city": "Boston"}, {"city": "Paris"}]
        assert sent["parallel_tool_calls"] is True


def test_capabilities_are_explicit_and_route_specific():
    with pytest.raises(ValidationError):
        ModelRoute(name="bad", app="a", capabilities=["everything"])
    request = replace(REQUEST, output_format=OutputFormat(SCHEMA))
    assert runner_payload(request, ROUTE)["response_format"]["type"] == "json_schema"
    with pytest.raises(UnsupportedFeature):
        runner_payload(request, OPTIONS.routes[1])
    for request in [
        replace(REQUEST, sampling=Sampling(top_p=0.5)),
        replace(REQUEST, hosted_search=True),
        replace(REQUEST, output_media=("image",)),
        replace(REQUEST, reasoning=True, reasoning_effort="low"),
        replace(REQUEST, reasoning=True, reasoning_budget=1024),
        replace(
            REQUEST,
            messages=(Message("assistant", (Reasoning("private", "signed"), Text("answer"))),),
        ),
    ]:
        with pytest.raises(UnsupportedFeature):
            runner_payload(request, ROUTE)


def test_reasoning_history_is_preserved_without_fabricating_signatures():
    request = replace(
        REQUEST,
        messages=(
            Message("assistant", (Reasoning("history"), Text("answer"))),
            Message("user", (Text("follow up"),)),
        ),
    )
    assert runner_payload(request, ROUTE)["messages"][0]["reasoning_content"] == "history"


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_invalid_structured_response_has_no_success(streaming, protocol):
    engine = scripted([frame({"content": '{"city":"Wrong"}'}), *ending()])
    options, _ = body("structured_output", protocol)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(LivepeerBackend(engine, OPTIONS))),
        base_url="http://test",
    ) as http:
        response = await http.post(
            "/v1/chat/completions" if protocol == "openai" else "/v1/messages",
            json={**options, "stream": streaming},
            headers={"anthropic-version": "2023-06-01"},
        )
    assert response.status_code == (200 if streaming else 502)
    assert "unsupported_response" in response.text
    assert "[DONE]" not in response.text and "message_stop" not in response.text


@pytest.mark.parametrize("feature", ["reasoning", "parallel_tools", "structured_output", "stop"])
async def test_extended_close_notifies_engine(monkeypatch, feature):
    closed = []
    original = FakeJobStream.disconnected

    async def record(self):
        closed.append(self)
        await original(self)

    monkeypatch.setattr(FakeJobStream, "disconnected", record)
    request = replace(
        REQUEST,
        reasoning=feature == "reasoning",
        parallel_tools=feature == "parallel_tools",
        output_format=OutputFormat(SCHEMA) if feature == "structured_output" else None,
        sampling=Sampling(stop=("END",)) if feature == "stop" else Sampling(),
    )
    events = LivepeerBackend(scripted(fixtures(feature)), OPTIONS).stream(request)
    await anext(events)
    await events.aclose()
    assert len(closed) == 1 and closed[0].closed and closed[0].disconnected_called


@pytest.mark.parametrize("cut", range(11))
def test_stop_markers_never_leak_across_fragment_boundaries(cut):
    f = StopFilter(("END", "FIN"))
    value = "hello END hidden"
    text = f.feed(value[:cut]) + f.feed(value[cut:]) + f.finish()
    assert text == "hello " and f.matched == "END"


def test_partial_stop_prefix_is_flushed_on_natural_finish():
    f = StopFilter(("ENDING",))
    assert f.feed("hello END") == "hello "
    assert f.finish() == "END" and f.matched is None


def data(delta, finish=None, stop=None):
    return json.dumps(
        {"choices": [{"index": 0, "delta": delta, "finish_reason": finish, "stop_reason": stop}]}
    )


def finish(decoder, reason="stop", stop=None):
    decoder.feed(data({}, reason, stop))
    decoder.feed('{"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":3}}')
    return decoder.feed("[DONE]")


def test_parallel_tool_arguments_stream_without_waiting_for_other_call():
    decoder = CompletionDecoder(1000, parallel_tools=True, allow_reasoning=True)
    assert decoder.feed(data({"reasoning": "think"}))[0].block == Reasoning("")
    out = decoder.feed(data(tool(5, "{", "a", "a")))
    assert out[0] == BlockEnd(0)
    assert decoder.feed(data(tool(9, "{", "b", "b")))[0].index == 2
    assert decoder.feed(data(tool(5, "}"))) == [Delta(1, "}")]
    assert decoder.feed(data(tool(9, "}"))) == [Delta(2, "}")]
    assert finish(decoder, "tool_calls")[-1] == Finished("tool_calls", 4, 3)


@pytest.mark.parametrize(
    "invalid",
    [
        "late_reasoning",
        "conflicting_reasoning",
        "refusal",
        "wrong_stop",
        "reasoning_without_answer",
        "duplicate_tool_id",
    ],
)
def test_invalid_extended_runner_responses(invalid):
    decoder = CompletionDecoder(
        1000,
        allow_reasoning=True,
        parallel_tools=True,
        stop_sequences=("END",) if invalid == "wrong_stop" else (),
    )
    with pytest.raises(BackendError):
        if invalid == "late_reasoning":
            decoder.feed(data({"content": "answer"}))
            decoder.feed(data({"reasoning": "too late"}))
        elif invalid == "conflicting_reasoning":
            decoder.feed(data({"reasoning": "a", "reasoning_content": "b"}))
        elif invalid == "refusal":
            decoder.feed(data({"refusal": "private refusal"}))
        elif invalid == "wrong_stop":
            finish(decoder, stop="other")
        elif invalid == "reasoning_without_answer":
            decoder.feed(data({"reasoning": "think"}))
            finish(decoder)
        else:
            decoder.feed(data(tool(1, "{}", "same", "a")))
            decoder.feed(data(tool(2, "{}", "same", "b")))
            finish(decoder, "tool_calls")


async def test_reasoning_and_structured_truncation_remain_length():
    engine = scripted([frame({"reasoning_content": "think"}), *ending("length")])
    result = await GenerationService(LivepeerBackend(engine, OPTIONS)).complete(
        replace(REQUEST, reasoning=True, output_format=OutputFormat(SCHEMA))
    )
    assert result.content == (Reasoning("think"),) and result.finish.reason == "length"


@pytest.mark.parametrize("feature", ["reasoning", "parallel_tools", "stop", "structured_output"])
@pytest.mark.parametrize("expire_deadline", [False, True])
async def test_real_engine_extended_disconnect_and_deadline(tmp_path, feature, expire_deadline):
    from livepeer_builder import ActorContext, EngineSupply
    from livepeer_builder.testing import ScriptedStreamTransport, StreamScript, make_engine

    from inference_gateway.core.backend import BackendDeadline

    first = fixtures(feature)[0]
    transport = ScriptedStreamTransport(opens=[StreamScript(events=[first, 10.0])])
    request = replace(
        REQUEST,
        reasoning=feature == "reasoning",
        parallel_tools=feature == "parallel_tools",
        output_format=OutputFormat(SCHEMA) if feature == "structured_output" else None,
        sampling=Sampling(stop=("END",)) if feature == "stop" else Sampling(),
    )
    async with make_engine(
        runners=[runner(ROUTE.app, metadata='{"model_name":"extended"}')],
        supply=EngineSupply(transport=transport),
        allocation_mode="per_application",
        shared_payment_key="fixture-key",
        shared_allocation_id="fixture-allocation",
        database_url=f"sqlite+aiosqlite:///{tmp_path}/engine.db",
        stream_settle_s=0.01,
    ) as engine:
        options = OPTIONS.model_copy(update={"idle_timeout_seconds": 0.05})
        service = GenerationService(LivepeerBackend(engine, options))
        if expire_deadline:
            with pytest.raises(BackendDeadline):
                await service.complete(request)
        else:
            events = service.stream(request)
            await anext(events)
            await events.aclose()
        actor = ActorContext(
            actor_id=options.actor_id,
            application_id=options.application_id,
            permissions=frozenset({"jobs.read"}),
        )
        jobs = await engine.jobs.list_jobs(actor)
        assert len(jobs) == 1 and jobs[0].state == "uncertain"
        assert transport.streams[0].closed and engine.health().running_jobs == 0
        assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("top_p", 0.0),
        ("top_k", 1),
        ("frequency_penalty", -0.5),
        ("presence_penalty", 0.5),
        ("seed", 0),
    ],
)
def test_sampling_controls_preserved_and_route_guarded(field, value):
    request = replace(REQUEST, sampling=Sampling(**{field: value}))
    with pytest.raises(UnsupportedFeature):
        runner_payload(request, ROUTE)
    route = ROUTE.model_copy(update={"capabilities": CAPS | {"sampling"}})
    payload = runner_payload(request, route)
    assert payload[field] == value
    assert all(
        k not in payload
        for k in {"top_p", "top_k", "frequency_penalty", "presence_penalty", "seed"} - {field}
    )


def test_reasoning_controls_require_explicit_route_support():
    route = ModelRoute(
        name="extended",
        app="test/chat",
        capabilities=CAPS | {"reasoning_budget"},
        reasoning_efforts={"low"},
    )
    request = replace(REQUEST, reasoning=True, reasoning_effort="low", reasoning_budget=1024)
    payload = runner_payload(request, route)
    assert payload["reasoning_effort"] == "low"
    assert payload["thinking_token_budget"] == 1024
    assert payload["chat_template_kwargs"] == {"enable_thinking": True}
    for bad in (
        replace(request, reasoning_effort="high"),
        replace(request, reasoning=False),
        replace(request, reasoning_effort="none"),
    ):
        with pytest.raises(UnsupportedFeature):
            runner_payload(bad, route)
    with pytest.raises(UnsupportedFeature):
        runner_payload(request, ROUTE)


@pytest.mark.parametrize(
    "kind,mime,wire",
    [
        (
            "image",
            "image/png",
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,YWJj", "detail": "high"},
            },
        ),
        (
            "audio",
            "audio/wav",
            {"type": "input_audio", "input_audio": {"data": "YWJj", "format": "wav"}},
        ),
    ],
)
def test_media_input_preserves_order_and_explicit_kind_support(kind, mime, wire):
    from inference_gateway.core.models import Media

    media = Media(kind, "YWJj", mime, detail="high")
    request = replace(REQUEST, messages=(Message("user", (Text("before"), media, Text("after"))),))
    route = ModelRoute(
        name="extended", app="test/chat", capabilities={"media_input"}, input_media={kind}
    )
    assert runner_payload(request, route)["messages"][0]["content"] == [
        {"type": "text", "text": "before"},
        wire,
        {"type": "text", "text": "after"},
    ]
    with pytest.raises(UnsupportedFeature):
        runner_payload(request, ROUTE)
    with pytest.raises(UnsupportedFeature):
        runner_payload(replace(request, messages=(Message("assistant", (media,)),)), route)


@pytest.mark.parametrize(
    "options",
    [
        {"reasoning_efforts": {"low"}},
        {"capabilities": {"reasoning_budget"}},
        {"capabilities": {"media_input"}},
        {"input_media": {"image"}},
    ],
)
def test_incoherent_route_controls_rejected(options):
    with pytest.raises(ValidationError):
        ModelRoute(name="bad", app="test/chat", **options)


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("feature", ["sampling", "reasoning_control", "image_input"])
async def test_remaining_request_controls_through_sdk(protocol, streaming, feature):
    route = ModelRoute(
        name="extended",
        app=ROUTE.app,
        capabilities=CAPS | {"sampling", "reasoning_budget", "media_input"},
        reasoning_efforts={"low"},
        input_media={"image"},
    )
    engine = scripted(
        fixtures("reasoning")
        if feature == "reasoning_control"
        else [frame({"content": "Hello"}), *ending()]
    )
    backend = LivepeerBackend(engine, LivepeerSettings(routes=(route,)))
    options = {"model": "extended", "messages": [{"role": "user", "content": "hello"}]}
    if protocol == "anthropic":
        options["max_tokens"] = 2048
    if feature == "sampling":
        options["top_p"] = 0.5
        if protocol == "anthropic":
            options["top_k"] = 3
        else:
            options.update(seed=0, presence_penalty=0.5, frequency_penalty=-0.5)
    elif feature == "reasoning_control":
        if protocol == "anthropic":
            options["thinking"] = {"type": "enabled", "budget_tokens": 1024}
        else:
            options["reasoning_effort"] = "low"
    else:
        from inference_gateway.testing import PIXEL

        media = (
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + PIXEL}}
            if protocol == "openai"
            else {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": PIXEL},
            }
        )
        options["messages"][0]["content"] = [{"type": "text", "text": "hello"}, media]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(backend))) as http:
        if protocol == "openai":
            client = AsyncOpenAI(api_key="local", base_url="http://test/v1", http_client=http)
            result = await client.chat.completions.create(**options, stream=streaming)
            if streaming:
                chunks = [c async for c in result]
                assert (
                    "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
                    == "Hello"
                )
            else:
                assert result.choices[0].message.content == "Hello"
        else:
            client = AsyncAnthropic(api_key="local", base_url="http://test", http_client=http)
            if streaming:
                async with client.messages.stream(**options) as stream:
                    result = await stream.get_final_message()
            else:
                result = await client.messages.create(**options)
            assert "".join(b.text for b in result.content if b.type == "text") == "Hello"
    sent = engine.jobs.requests[0][1].payload
    assert len(engine.jobs.requests) == 1
    if feature == "sampling":
        assert sent["top_p"] == 0.5
        assert sent["top_k" if protocol == "anthropic" else "seed"] == (
            3 if protocol == "anthropic" else 0
        )
    elif feature == "reasoning_control":
        assert sent["thinking_token_budget" if protocol == "anthropic" else "reasoning_effort"] == (
            1024 if protocol == "anthropic" else "low"
        )
    else:
        assert sent["messages"][0]["content"][1]["image_url"]["url"].startswith(
            "data:image/png;base64,"
        )
