"""Paid acceptance checks against a running gateway; no automatic retries."""

import asyncio
import json
import os
import time

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

MODEL = os.getenv("GATEWAY_MODEL_NAME", "qwen3.8-27b")
BASE = os.getenv("GATEWAY_TEST_URL", "http://127.0.0.1:8000")
SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
    "additionalProperties": False,
}


async def openai_case(client, streaming, tools=False, followup=None):
    messages = [{"role": "user", "content": "Say hello in one short sentence."}]
    args = {"model": MODEL, "max_tokens": 160, "temperature": 0, "stream": streaming}
    if streaming:
        args["stream_options"] = {"include_usage": True}
    if tools:
        messages = [{"role": "user", "content": "Use weather to get the weather in Boston."}]
        args["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": "weather",
                    "description": "Get current weather",
                    "parameters": SCHEMA,
                },
            }
        ]
        args["tool_choice"] = {"type": "function", "function": {"name": "weather"}}
    if followup:
        messages += [
            followup,
            {
                "role": "tool",
                "tool_call_id": followup["tool_calls"][0]["id"],
                "content": "Boston is sunny, 21 degrees Celsius.",
            },
        ]
        args["tool_choice"] = "none"
    result = await client.chat.completions.create(messages=messages, **args)
    text, calls, finish, usage, chunks = "", {}, None, None, 0
    if streaming:
        async with result:
            async for chunk in result:
                chunks += 1
                usage = chunk.usage or usage
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                finish = choice.finish_reason or finish
                text += choice.delta.content or ""
                for part in choice.delta.tool_calls or []:
                    call = calls.setdefault(
                        part.index,
                        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                    )
                    call["id"] += part.id or ""
                    if part.function:
                        call["function"]["name"] += part.function.name or ""
                        call["function"]["arguments"] += part.function.arguments or ""
    else:
        choice = result.choices[0]
        text, finish, usage = choice.message.content or "", choice.finish_reason, result.usage
        calls = {
            i: t.model_dump(exclude_none=True)
            for i, t in enumerate(choice.message.tool_calls or [])
        }
    assert usage and usage.prompt_tokens > 0 and usage.completion_tokens > 0
    if tools and not followup:
        assert finish == "tool_calls" and len(calls) == 1
        call = calls[0]
        assert call["id"] and call["function"]["name"] == "weather"
        assert json.loads(call["function"]["arguments"])["city"].lower() == "boston"
        return {"role": "assistant", "content": None, "tool_calls": [call]}
    assert text.strip() and finish == "stop"
    if followup:
        assert "sunny" in text.lower() and "21" in text
    if streaming:
        assert chunks > 1


async def anthropic_case(client, streaming, tools=False, followup=None):
    messages = [{"role": "user", "content": "Say hello in one short sentence."}]
    args = {"model": MODEL, "max_tokens": 160, "temperature": 0}
    if tools:
        messages = [{"role": "user", "content": "Use weather to get the weather in Boston."}]
        args["tools"] = [
            {"name": "weather", "description": "Get current weather", "input_schema": SCHEMA}
        ]
        args["tool_choice"] = {"type": "tool", "name": "weather"}
    if followup:
        messages += [
            followup,
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": next(
                            b["id"] for b in followup["content"] if b["type"] == "tool_use"
                        ),
                        "content": "Boston is sunny, 21 degrees Celsius.",
                    }
                ],
            },
        ]
        args["tool_choice"] = {"type": "none"}
    if streaming:
        async with client.messages.stream(messages=messages, **args) as stream:
            events = [event async for event in stream]
            result = await stream.get_final_message()
        assert len(events) > 1
    else:
        result = await client.messages.create(messages=messages, **args)
    assert result.usage.input_tokens > 0 and result.usage.output_tokens > 0
    if tools and not followup:
        calls = [b for b in result.content if b.type == "tool_use"]
        assert result.stop_reason == "tool_use" and len(calls) == 1
        assert calls[0].id and calls[0].name == "weather"
        assert calls[0].input["city"].lower() == "boston"
        return {
            "role": "assistant",
            "content": [b.model_dump(exclude_none=True) for b in result.content],
        }
    text = "".join(b.text for b in result.content if b.type == "text")
    assert text.strip() and result.stop_reason == "end_turn"
    if followup:
        assert "sunny" in text.lower() and "21" in text


async def main():
    key = os.getenv("GATEWAY_API_KEY", "local")
    clients = [
        (
            "openai",
            AsyncOpenAI(base_url=BASE + "/v1", api_key=key, max_retries=0, timeout=310),
            openai_case,
        ),
        (
            "anthropic",
            AsyncAnthropic(base_url=BASE, api_key=key, max_retries=0, timeout=310),
            anthropic_case,
        ),
    ]
    failures = 0
    for protocol, client, run in clients:
        async with client:
            for streaming in [False, True]:
                for scenario in ["text", "tool_call"]:
                    start = time.monotonic()
                    label = f"{protocol}/{scenario}/{'stream' if streaming else 'json'}"
                    try:
                        followup = await run(client, streaming, tools=scenario == "tool_call")
                        print(f"PASS {label} {time.monotonic() - start:.1f}s", flush=True)
                        if followup:
                            label = f"{protocol}/tool_result/{'stream' if streaming else 'json'}"
                            await run(client, streaming, tools=True, followup=followup)
                            print(
                                f"PASS {protocol}/tool_result/{'stream' if streaming else 'json'}",
                                flush=True,
                            )
                    except Exception as error:
                        failures += 1
                        # Never dump exception bodies, credentials, or prompts.
                        print(
                            f"FAIL {label} {type(error).__name__} "
                            f"status={getattr(error, 'status_code', None)}",
                            flush=True,
                        )
    if failures:
        raise SystemExit(f"{failures} live scenario groups failed")
    print("All 12 live scenarios passed", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
