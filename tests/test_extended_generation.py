"""Public contracts for extended generation, independent of paid model availability."""

import base64
import io
import json
import wave
from dataclasses import replace

import httpx
import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from inference_gateway.adapters.schemas import AnthropicRequest, OpenAIRequest
from inference_gateway.adapters.translate import anthropic_request, openai_request
from inference_gateway.app import create_app
from inference_gateway.backends.livepeer_request import runner_payload
from inference_gateway.config import ModelRoute
from inference_gateway.core.backend import BackendError, UnsupportedFeature
from inference_gateway.core.models import (
    BlockEnd,
    BlockStart,
    Delta,
    Finished,
    Generate,
    HostedResult,
    Media,
    Message,
    OutputFormat,
    Reasoning,
    Sampling,
    Text,
    ToolCall,
)
from inference_gateway.core.service import GenerationService
from inference_gateway.testing import PIXEL, FakeBackend, silence

SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string", "enum": ["Boston", "Paris"]},
        "count": {"type": "integer"},
        "items": {"type": "array", "items": {"type": "boolean"}},
    },
    "required": ["city", "count", "items"],
    "additionalProperties": False,
}
SOURCE = {"url": "https://example.com/source", "title": "Fixture", "text": "The sky is blue."}


@pytest.fixture
async def clients():
    backend = FakeBackend({"a": {"city": "Boston"}, "b": {"count": 2}})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(backend)), base_url="http://test"
    ) as http:
        yield (
            http,
            AsyncOpenAI(api_key="fake", base_url="http://test/v1", http_client=http),
            AsyncAnthropic(api_key="fake", base_url="http://test", http_client=http),
        )


def normalized(blocks):
    return [b.model_dump(exclude_none=True, exclude={"parsed_output"}) for b in blocks]


def args(protocol):
    value = {"model": "fake", "messages": [{"role": "user", "content": "hello"}]}
    if protocol == "anthropic":
        value["max_tokens"] = 2048
    return value


def tool_options(protocol):
    if protocol == "openai":
        return {
            "tools": [{"type": "function", "function": {"name": n}} for n in ("a", "b")],
            "parallel_tool_calls": True,
        }
    return {
        "tools": [{"name": n, "input_schema": {"type": "object"}} for n in ("a", "b")],
        "tool_choice": {"type": "auto", "disable_parallel_tool_use": False},
    }


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_reasoning_stream_and_history(clients, protocol):
    _, oa, an = clients
    options = args(protocol)
    options.update(
        {"reasoning_effort": "low"}
        if protocol == "openai"
        else {"thinking": {"type": "enabled", "budget_tokens": 1024}}
    )
    if protocol == "openai":
        full = await oa.chat.completions.create(**options)
        stream = await oa.chat.completions.create(**options, stream=True)
        chunks = [c async for c in stream]
        assert (
            "".join(getattr(c.choices[0].delta, "reasoning_content", "") for c in chunks)
            == full.choices[0].message.reasoning_content
        )
        assert full.choices[0].message.content == "Echo: hello"
        history = {
            "role": "assistant",
            "content": full.choices[0].message.content,
            "reasoning_content": full.choices[0].message.reasoning_content,
        }
    else:
        full = await an.messages.create(**options)
        async with an.messages.stream(**options) as stream:
            final = await stream.get_final_message()
        assert normalized(final.content) == normalized(full.content)
        assert final.content[0].type == "thinking"
        assert final.content[0].signature == "fake-signature"
        history = {
            "role": "assistant",
            "content": [b.model_dump(exclude_none=True) for b in full.content],
        }
    options["messages"] += [history, {"role": "user", "content": "again"}]
    result = await (
        oa.chat.completions.create(**options)
        if protocol == "openai"
        else an.messages.create(**options)
    )
    assert result


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_structured_output_stream_and_truncation(clients, protocol):
    _, oa, an = clients
    options = args(protocol)
    options.update(
        {
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "sample", "strict": True, "schema": SCHEMA},
            }
        }
        if protocol == "openai"
        else {"output_config": {"format": {"type": "json_schema", "schema": SCHEMA}}}
    )
    if protocol == "openai":
        full = await oa.chat.completions.create(**options)
        stream = await oa.chat.completions.create(**options, stream=True)
        text = "".join([c.choices[0].delta.content or "" async for c in stream])
        assert text == full.choices[0].message.content
        limited = await oa.chat.completions.create(**options, max_tokens=3)
        assert limited.choices[0].finish_reason == "length"
    else:
        full = await an.messages.create(**options)
        async with an.messages.stream(**options) as stream:
            text = "".join([t async for t in stream.text_stream])
        assert text == full.content[0].text
        limited = await an.messages.create(**{**options, "max_tokens": 3})
        assert limited.stop_reason == "max_tokens"
    assert json.loads(text) == {"city": "Boston", "count": 0, "items": []}


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_citations_stream(clients, protocol):
    _, oa, an = clients
    options = args(protocol)
    if protocol == "openai":
        full = await oa.chat.completions.create(
            **options, extra_body={"gateway": {"sources": [SOURCE]}}
        )
        stream = await oa.chat.completions.create(
            **options, stream=True, extra_body={"gateway": {"sources": [SOURCE]}}
        )
        chunks = [c async for c in stream]
        annotations = [
            a for c in chunks for a in (getattr(c.choices[0].delta, "annotations", None) or [])
        ]
        assert (
            annotations[0]["url_citation"]["url"]
            == full.choices[0].message.annotations[0].url_citation.url
        )
        assert full.choices[0].message.content == SOURCE["text"]
    else:
        options["messages"][0]["content"] = [
            {
                "type": "document",
                "source": {"type": "text", "media_type": "text/plain", "data": SOURCE["text"]},
                "title": "Fixture",
                "citations": {"enabled": True},
            },
            {"type": "text", "text": "quote"},
        ]
        full = await an.messages.create(**options)
        async with an.messages.stream(**options) as stream:
            final = await stream.get_final_message()
        assert normalized(final.content) == normalized(full.content)
        citation = final.content[0].citations[0]
        assert citation.type == "char_location" and citation.document_index == 0
        assert citation.cited_text == SOURCE["text"]
        options["messages"] += [
            {
                "role": "assistant",
                "content": [p.model_dump(exclude_none=True) for p in full.content],
            },
            {"role": "user", "content": "again"},
        ]
        assert await an.messages.create(**options)


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_hosted_tool_is_not_client_tool_call(clients, protocol):
    _, oa, an = clients
    options = args(protocol)
    if protocol == "openai":
        extra = {"gateway": {"hosted_tools": ["web_search"]}}
        full = await oa.chat.completions.create(**options, extra_body=extra)
        assert not full.choices[0].message.tool_calls
        blocks = full.choices[0].message.gateway_blocks
        stream = await oa.chat.completions.create(**options, stream=True, extra_body=extra)
        streamed = [
            b async for c in stream for b in getattr(c.choices[0].delta, "gateway_blocks", [])
        ]
        assert streamed == blocks
        assert full.choices[0].finish_reason == "stop"
    else:
        options["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}]
        full = await an.messages.create(**options)
        async with an.messages.stream(**options) as stream:
            final = await stream.get_final_message()
        assert normalized(final.content) == normalized(full.content)
        assert [b.type for b in full.content] == [
            "server_tool_use",
            "web_search_tool_result",
            "text",
        ]
        assert full.stop_reason == "end_turn"
        options["messages"] += [
            {
                "role": "assistant",
                "content": [p.model_dump(exclude_none=True) for p in full.content],
            },
            {"role": "user", "content": "again"},
        ]
        assert await an.messages.create(**options)


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_parallel_tool_sdk_assembly(clients, protocol):
    _, oa, an = clients
    options = {**args(protocol), **tool_options(protocol)}
    if protocol == "openai":
        full = await oa.chat.completions.create(**options)
        stream = await oa.chat.completions.create(**options, stream=True)
        calls = {}
        order = []
        async for chunk in stream:
            for part in chunk.choices[0].delta.tool_calls or []:
                order.append(part.index)
                calls[part.index] = calls.get(part.index, "") + (part.function.arguments or "")
        assert order[:2] == [0, 1]
        assert [json.loads(calls[i]) for i in sorted(calls)] == [
            json.loads(c.function.arguments) for c in full.choices[0].message.tool_calls
        ]
    else:
        full = await an.messages.create(**options)
        async with an.messages.stream(**options) as stream:
            events = [e async for e in stream]
            final = await stream.get_final_message()
        assert normalized(final.content) == normalized(full.content)
        starts = [e.index for e in events if e.type == "content_block_start"]
        assert starts == [0, 1]
        assert [b.input for b in final.content] == [{"city": "Boston"}, {"count": 2}]


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_media_input_and_output(clients, protocol):
    _, oa, an = clients
    options = args(protocol)
    image = (
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + PIXEL}}
        if protocol == "openai"
        else {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": PIXEL},
        }
    )
    options["messages"][0]["content"] = [
        image,
        {"type": "input_audio", "input_audio": {"data": silence("wav"), "format": "wav"}},
    ]
    extra = {"gateway": {"output_media": ["image", "audio"]}}
    if protocol == "openai":
        full = await oa.chat.completions.create(**options, extra_body=extra)
        stream = await oa.chat.completions.create(**options, stream=True, extra_body=extra)
        chunks = [c async for c in stream]
        data = full.choices[0].message.audio.data
        assert any(getattr(c.choices[0].delta, "audio", None) for c in chunks)
        assert full.choices[0].message.gateway_blocks[0]["block"]["data"] == PIXEL
        assert "image, audio" in full.choices[0].message.content
    else:
        full = await an.messages.create(**options, extra_body=extra)
        async with an.messages.stream(**options, extra_body=extra) as stream:
            final = await stream.get_final_message()
        assert normalized(final.content) == normalized(full.content)
        assert full.content[1].data == PIXEL
        data = full.content[2].data
    with wave.open(io.BytesIO(base64.b64decode(data))) as audio:
        assert audio.getnframes() == 240


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_sampling_forwarded_and_stop_applied(clients, protocol):
    http, _, _ = clients
    body = args(protocol)
    body.update(
        {
            "top_p": 0.7,
            "stop": ["lo"],
            "seed": 42,
            "presence_penalty": 0.5,
            "frequency_penalty": -0.5,
            "gateway": {"top_k": 8},
        }
        if protocol == "openai"
        else {"top_p": 0.7, "top_k": 8, "stop_sequences": ["lo"]}
    )
    command = (
        openai_request(OpenAIRequest.model_validate(body))
        if protocol == "openai"
        else anthropic_request(AnthropicRequest.model_validate(body))
    )
    assert command.sampling.top_p == 0.7 and command.sampling.top_k == 8
    if protocol == "openai":
        assert command.sampling.seed == 42
    route = "/v1/chat/completions" if protocol == "openai" else "/v1/messages"
    result = await http.post(route, json=body, headers={"anthropic-version": "2023-06-01"})
    assert result.status_code == 200
    data = result.json()
    if protocol == "openai":
        assert data["choices"][0]["message"]["content"] == "Echo: hel"
    else:
        assert data["content"][0]["text"] == "Echo: hel"
        assert data["stop_reason"] == "stop_sequence" and data["stop_sequence"] == "lo"


@pytest.mark.parametrize(
    "change",
    [
        {"top_p": 1.1},
        {"stop": [""]},
        {"stop": ["a"] * 5},
        {"frequency_penalty": 3},
        {"gateway": {"top_k": 0}},
        {"modalities": ["audio"]},
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_audio", "input_audio": {"data": "%%%", "format": "wav"}}
                    ],
                }
            ]
        },
        {
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "bad",
                    "schema": {"type": "object", "$ref": "https://example.com"},
                },
            }
        },
        {"response_format": {"type": "json_object"}, "gateway": {"hosted_tools": ["web_search"]}},
    ],
)
async def test_invalid_feature_requests_rejected(clients, change):
    http, _, _ = clients
    result = await http.post("/v1/chat/completions", json={**args("openai"), **change})
    assert result.status_code == 400


@pytest.mark.parametrize("stream", [False, True])
async def test_invalid_structured_backend_response_has_explicit_error(stream):
    class Invalid:
        closed = False

        async def stream(self, request):
            try:
                yield BlockStart(0)
                yield Delta(0, '{"unexpected":true}')
                yield BlockEnd(0)
                yield Finished("stop")
            finally:
                self.closed = True

    backend = Invalid()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(backend)), base_url="http://test"
    ) as http:
        result = await http.post(
            "/v1/chat/completions",
            json={
                **args("openai"),
                "stream": stream,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "sample", "schema": SCHEMA},
                },
            },
        )
    assert backend.closed
    assert "Unsupported Response" in result.text and "unsupported_response" in result.text
    assert "[DONE]" not in result.text
    assert result.status_code == (200 if stream else 502)


@pytest.mark.parametrize(
    "change",
    [
        {"reasoning": True},
        {"sampling": Sampling(top_p=0.5)},
        {"output_format": OutputFormat(SCHEMA)},
        {"parallel_tools": True},
        {"hosted_search": True},
        {"output_media": ("image",)},
        {"messages": (Message("user", (Media("image", PIXEL, "image/png"),)),)},
        {"messages": (Message("assistant", (Reasoning("history"), Text("answer"))),)},
    ],
)
def test_livepeer_rejects_unverified_features_before_payload(change):
    request = replace(Generate("fake", (Message("user", (Text("hello"),)),)), **change)
    with pytest.raises(UnsupportedFeature):
        runner_payload(request, ModelRoute(name="fake", app="test/chat"))


@pytest.mark.parametrize(
    "events",
    [
        [BlockStart(0), BlockStart(0)],
        [BlockStart(0), BlockEnd(0), Delta(0, "late")],
        [BlockStart(0, ToolCall("a", "x", "")), BlockStart(1, ToolCall("a", "y", ""))],
        [BlockStart(0, block=HostedResult("unknown", ()))],
        [BlockStart(0), Delta(0, "x"), Finished("stop")],
    ],
)
async def test_interleaved_invalid_events_close_backend(events):
    class Invalid:
        closed = False

        async def stream(self, request):
            try:
                for e in events:
                    yield e
            finally:
                self.closed = True

    backend = Invalid()
    with pytest.raises(BackendError):
        await GenerationService(backend).complete(
            Generate("fake", (Message("user", (Text("x"),)),))
        )
    assert backend.closed


async def test_openai_hosted_history_and_native_audio_only(clients):
    http, oa, _ = clients
    options = args("openai")
    full = await oa.chat.completions.create(
        **options, extra_body={"gateway": {"hosted_tools": ["web_search"]}}
    )
    message = full.choices[0].message
    options["messages"] += [
        {"role": "assistant", "content": message.content, "gateway_blocks": message.gateway_blocks},
        {"role": "user", "content": "again"},
    ]
    assert (await oa.chat.completions.create(**options)).choices[0].message.content == "Echo: again"
    for streaming in (False, True):
        result = await http.post(
            "/v1/chat/completions",
            json={
                **args("openai"),
                "stream": streaming,
                "modalities": ["audio"],
                "audio": {"format": "pcm16", "voice": "alloy"},
            },
        )
        assert result.status_code == 200
        if streaming:
            frames = [
                json.loads(line[6:])
                for line in result.text.splitlines()
                if line.startswith("data: {")
            ]
            assert not any(f["choices"][0]["delta"].get("content") for f in frames)
            assert any(f["choices"][0]["delta"].get("audio") for f in frames)
        else:
            assert result.json()["choices"][0]["message"]["content"] is None
            assert (
                base64.b64decode(result.json()["choices"][0]["message"]["audio"]["data"])
                == b"\0\0" * 240
            )


async def test_anthropic_media_history(clients):
    _, _, an = clients
    options = args("anthropic")
    full = await an.messages.create(
        **options, extra_body={"gateway": {"output_media": ["image", "audio"]}}
    )
    options["messages"] += [
        {"role": "assistant", "content": normalized(full.content)},
        {"role": "user", "content": "again"},
    ]
    assert (await an.messages.create(**options)).content[0].text == "Echo: again"


def test_existing_anthropic_tool_choice_does_not_implicitly_enable_parallelism():
    body = AnthropicRequest.model_validate(
        {
            **args("anthropic"),
            "tools": [{"name": "a", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "a"},
        }
    )
    assert not anthropic_request(body).parallel_tools


async def test_cancelling_an_interleaved_stream_closes_backend():
    class Pausing:
        closed = False

        async def stream(self, request):
            try:
                yield BlockStart(0, ToolCall("a", "a", ""))
                yield BlockStart(1, ToolCall("b", "b", ""))
                yield Delta(0, '{"unfinished":')
            finally:
                self.closed = True

    backend = Pausing()
    stream = GenerationService(backend).stream(Generate("fake", (Message("user", (Text("x"),)),)))
    await anext(stream)
    await anext(stream)
    await anext(stream)
    await stream.aclose()
    assert backend.closed


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_livepeer_feature_guard_is_public_and_never_dispatches(protocol, streaming):
    from livepeer_builder.testing import FakeEngine

    from inference_gateway.backends.livepeer import LivepeerBackend
    from inference_gateway.config import LivepeerSettings

    engine = FakeEngine(streaming=True)
    backend = LivepeerBackend(
        engine, LivepeerSettings(routes=(ModelRoute(name="fake", app="test/chat"),))
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(backend)), base_url="http://test"
    ) as http:
        result = await http.post(
            "/v1/chat/completions" if protocol == "openai" else "/v1/messages",
            json={**args(protocol), "stream": streaming, "gateway": {"reasoning": True}},
            headers={"anthropic-version": "2023-06-01"},
        )
    assert result.status_code == 400
    assert result.json()["error"]["code"] == "unsupported_feature"
    assert result.json()["error"]["type"] == "invalid_request_error"
    assert not engine.jobs.requests


@pytest.mark.parametrize(
    "block",
    [Media("audio", "%%%", "audio/wav"), Media("image", PIXEL, "image/unknown"), Text("", ())],
)
async def test_invalid_media_and_citation_output_is_rejected(block):
    from inference_gateway.core.backend import UnsupportedResponse
    from inference_gateway.core.models import Citation

    if isinstance(block, Text):
        block = Text("", (Citation("bad", "not present", "https://example.com", end=20),))

    class Invalid:
        async def stream(self, request):
            yield BlockStart(0, block=block)
            yield BlockEnd(0)
            yield Finished("stop")

    with pytest.raises(UnsupportedResponse):
        await GenerationService(Invalid()).complete(
            Generate("fake", (Message("user", (Text("x"),)),))
        )
