import asyncio
import json

import httpx
import pytest

from endpoint_gateway.app import Settings, create_app
from endpoint_gateway.core.backend import BackendError
from endpoint_gateway.core.models import (
    BlockEnd,
    BlockStart,
    Delta,
    Finished,
    Generate,
    Message,
    Text,
)
from endpoint_gateway.core.service import GenerationService
from endpoint_gateway.testing import FakeBackend


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        yield client


@pytest.mark.parametrize(
    "path,headers,body",
    [
        (
            "/v1/chat/completions",
            {},
            {"model": "fake", "messages": [{"role": "user", "content": "x"}]},
        ),
        (
            "/v1/messages",
            {"anthropic-version": "2023-06-01"},
            {"model": "fake", "max_tokens": 40, "messages": [{"role": "user", "content": "x"}]},
        ),
    ],
)
async def test_unknown_fields_and_malformed_json(client, path, headers, body):
    body["unsupported"] = True
    response = await client.post(path, json=body, headers=headers)
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert response.headers["x-request-id"]
    response = await client.post(path, content="{", headers=headers)
    assert response.status_code == 400


async def test_auth_and_anthropic_version():
    app = create_app(settings=Settings(api_key="secret"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        assert (await c.post("/v1/messages", json={})).status_code == 401
        assert (
            await c.post("/v1/messages", json={}, headers={"x-api-key": "secret"})
        ).status_code == 400
        assert (await c.get("/healthz")).json() == {"status": "ok"}


@pytest.mark.parametrize(
    "change",
    [
        {"tool_choice": "required"},
        {"messages": []},
        {"max_tokens": 0},
        {"n": 2},
        {"messages": [{"role": "tool", "tool_call_id": "missing", "content": "x"}]},
        {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}]},
    ],
)
async def test_rejected_requests(client, change):
    body = {"model": "fake", "messages": [{"role": "user", "content": "hi"}], **change}
    assert (await client.post("/v1/chat/completions", json=body)).status_code == 400


async def test_tool_choice_none_and_named(client):
    body = {
        "model": "fake",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": name}} for name in ["a", "b"]],
        "tool_choice": "none",
    }
    result = await client.post("/v1/chat/completions", json=body)
    assert result.json()["choices"][0]["message"]["content"] == "Echo: hi"
    body["tool_choice"] = {"type": "function", "function": {"name": "b"}}
    result = await client.post("/v1/chat/completions", json=body)
    assert result.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "b"


class BrokenBackend:
    def __init__(self, after_start=False, slow=False):
        self.after_start, self.slow, self.closed = after_start, slow, False

    async def stream(self, request):
        try:
            if self.after_start:
                yield BlockStart(0)
                yield Delta(0, "partial")
            if self.slow:
                await asyncio.sleep(10)
            raise BackendError("secret upstream detail")
        finally:
            self.closed = True


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("slow", [False, True])
async def test_pre_start_failures_and_timeouts(stream, slow):
    backend = BrokenBackend(slow=slow)
    app = create_app(backend, Settings(timeout_seconds=0.05))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        result = await c.post(
            "/v1/chat/completions",
            json={
                "model": "fake",
                "stream": stream,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert result.status_code == (504 if slow else 502)
    assert "secret" not in result.text
    assert backend.closed


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_midstream_failure_has_no_success_terminator(protocol):
    backend = BrokenBackend(after_start=True)
    app = create_app(backend)
    path = "/v1/chat/completions" if protocol == "openai" else "/v1/messages"
    body = {"model": "fake", "stream": True, "messages": [{"role": "user", "content": "hi"}]}
    if protocol == "anthropic":
        body["max_tokens"] = 100
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        result = await c.post(path, json=body, headers={"anthropic-version": "2023-06-01"})
    assert result.status_code == 200
    assert '"error"' in result.text
    assert "secret" not in result.text
    assert "[DONE]" not in result.text and "message_stop" not in result.text
    assert backend.closed


async def test_close_propagates_to_backend():
    backend = BrokenBackend(after_start=True)
    stream = GenerationService(backend).stream(Generate("fake", (Message("user", (Text("hi"),)),)))
    await anext(stream)
    await stream.aclose()
    assert backend.closed


async def test_concurrent_requests_do_not_share_state(client):
    async def send(text):
        response = await client.post(
            "/v1/chat/completions",
            json={"model": "fake", "messages": [{"role": "user", "content": text}]},
        )
        return response.json()

    results = await asyncio.gather(*(send(str(i)) for i in range(10)))
    assert len({r["id"] for r in results}) == 10
    assert [r["choices"][0]["message"]["content"] for r in results] == [
        f"Echo: {i}" for i in range(10)
    ]


async def test_fake_stream_matches_completion_and_length():
    service = GenerationService(FakeBackend())
    request = Generate("fake", (Message("user", (Text("hello"),)),), max_tokens=4)
    result = await service.complete(request)
    assert result.content == (Text("Echo"),)
    assert result.finish.reason == "length"


class MixedBackend:
    async def stream(self, request):
        from endpoint_gateway.core.models import ToolCall

        yield BlockStart(0)
        yield Delta(0, "Checking")
        yield BlockEnd(0)
        for i in range(1, 3):
            yield BlockStart(i, ToolCall(f"call_{i}", "lookup", ""))
            yield Delta(i, '{"x":')
            yield Delta(i, f"{i}}}")
            yield BlockEnd(i)
        yield Finished("tool_calls", 12, 8)


async def test_mixed_text_and_multiple_tools_use_tool_indices():
    app = create_app(MixedBackend())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post(
            "/v1/chat/completions",
            json={"model": "fake", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        )
    frames = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: {")]
    calls = [call for f in frames for call in f["choices"][0]["delta"].get("tool_calls", [])]
    assert {call["index"] for call in calls} == {0, 1}


@pytest.mark.parametrize(
    "events",
    [
        [Delta(0, "orphan"), Finished("stop")],
        [BlockStart(1), BlockEnd(1), Finished("stop")],
        [BlockStart(0), Finished("stop")],
        [BlockStart(0), Delta(0, "partial"), BlockEnd(0)],
        [Finished("stop"), Delta(0, "late")],
    ],
)
async def test_invalid_backend_stream_is_rejected(events):
    class InvalidBackend:
        async def stream(self, request):
            for event in events:
                yield event

    request = Generate("fake", (Message("user", (Text("hi"),)),))
    with pytest.raises(BackendError):
        await GenerationService(InvalidBackend()).complete(request)


async def test_explicit_tool_named_none_is_distinct_from_disabling_tools(client):
    response = await client.post(
        "/v1/chat/completions",
        json={
            "model": "fake",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "none"}}],
            "tool_choice": {"type": "function", "function": {"name": "none"}},
        },
    )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "none"
