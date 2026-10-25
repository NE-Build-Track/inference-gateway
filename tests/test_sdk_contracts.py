import json

import httpx
import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from endpoint_gateway.app import create_app
from endpoint_gateway.testing import FakeBackend


@pytest.fixture
async def clients():
    app = create_app(FakeBackend({"weather": {"city": "Boston"}}))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
        yield (
            AsyncOpenAI(api_key="fake", base_url="http://test/v1", http_client=http),
            AsyncAnthropic(api_key="fake", base_url="http://test", http_client=http),
        )


async def test_openai_text_and_usage_stream(clients):
    client, _ = clients
    args = {"model": "fake-model", "messages": [{"role": "user", "content": "hello"}]}
    full = await client.chat.completions.create(**args)
    assert full.choices[0].message.content == "Echo: hello"
    stream = await client.chat.completions.create(
        **args, stream=True, stream_options={"include_usage": True}
    )
    chunks = [chunk async for chunk in stream]
    assert "".join(c.choices[0].delta.content or "" for c in chunks if c.choices) == "Echo: hello"
    assert chunks[-2].choices[0].finish_reason == "stop"
    assert chunks[-1].choices == []
    assert chunks[-1].usage == full.usage
    assert len({c.id for c in chunks}) == 1


async def test_openai_stream_tool_and_result(clients):
    client, _ = clients
    tools = [
        {
            "type": "function",
            "function": {
                "name": "weather",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ]
    messages = [{"role": "user", "content": "weather?"}]
    args = {"model": "fake-model", "messages": messages, "tools": tools}
    full = await client.chat.completions.create(**args)
    call = full.choices[0].message.tool_calls[0]
    assert full.choices[0].finish_reason == "tool_calls"
    stream = await client.chat.completions.create(**args, stream=True)
    chunks = [c async for c in stream]
    deltas = [tc for c in chunks for tc in (c.choices[0].delta.tool_calls or [])]
    assert deltas[0].id == call.id
    assert deltas[0].function.name == "weather"
    arguments = "".join(d.function.arguments or "" for d in deltas)
    assert json.loads(arguments) == {"city": "Boston"}
    messages.extend(
        [
            {"role": "assistant", "tool_calls": [call.model_dump()]},
            {"role": "tool", "tool_call_id": call.id, "content": "Sunny"},
        ]
    )
    result = await client.chat.completions.create(**args)
    assert result.choices[0].message.content == "Tool result: Sunny"


async def test_anthropic_text_stream_helper(clients):
    _, client = clients
    args = {
        "model": "fake-model",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "hello"}],
    }
    full = await client.messages.create(**args)
    assert full.content[0].text == "Echo: hello"
    async with client.messages.stream(**args) as stream:
        parts = [text async for text in stream.text_stream]
        final = await stream.get_final_message()
    assert "".join(parts) == full.content[0].text
    assert final.content[0].text == full.content[0].text
    assert final.content[0].type == full.content[0].type
    assert final.stop_reason == "end_turn"
    assert final.usage == full.usage


async def test_anthropic_tool_stream_and_result(clients):
    _, client = clients
    messages = [{"role": "user", "content": "weather?"}]
    args = {
        "model": "fake-model",
        "max_tokens": 1024,
        "messages": messages,
        "tools": [{"name": "weather", "input_schema": {"type": "object"}}],
    }
    full = await client.messages.create(**args)
    async with client.messages.stream(**args) as stream:
        events = [e async for e in stream]
        final = await stream.get_final_message()
    assert final.content == full.content
    assert final.content[0].input == {"city": "Boston"}
    assert final.stop_reason == "tool_use"
    assert events[0].type == "message_start"
    assert events[-1].type == "message_stop"
    call = final.content[0]
    messages.extend(
        [
            {"role": "assistant", "content": [call.model_dump()]},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": call.id, "content": "Sunny"}],
            },
        ]
    )
    result = await client.messages.create(**args)
    assert result.content[0].text == "Tool result: Sunny"
