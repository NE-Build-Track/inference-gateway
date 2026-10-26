"""Paid end-to-end acceptance for explicitly enabled Livepeer routes.

Uses both official SDKs, disables retries, and writes sanitized evidence after each
case. No prompts, generated content, tool arguments, keys or raw exceptions are logged.
"""

import argparse
import asyncio
import inspect
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

FEATURES = ("structured_output", "reasoning", "stop", "parallel_tools")
SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "enum": ["blue"]},
        "detail": {
            "type": "object",
            "properties": {"ok": {"type": "boolean", "enum": [True]}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    },
    "required": ["answer", "detail"],
    "additionalProperties": False,
}


def request(feature, protocol, model):
    body = {
        "model": model,
        "max_tokens": 512 if feature == "reasoning" else 192,
        "messages": [{"role": "user", "content": "Return a short answer."}],
    }
    extra = {}
    if feature == "structured_output":
        body["messages"][0]["content"] = "Return the requested JSON object."
        body.update(
            {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "acceptance", "strict": True, "schema": SCHEMA},
                }
            }
            if protocol == "openai"
            else {"output_config": {"format": {"type": "json_schema", "schema": SCHEMA}}}
        )
    elif feature == "reasoning":
        body["messages"][0]["content"] = "What is 17 times 23? Think briefly, then give the answer."
        extra = {"gateway": {"reasoning": True}}
    elif feature == "stop":
        body["messages"][0]["content"] = "Output exactly these words: FIRST SECOND THIRD"
        body["stop" if protocol == "openai" else "stop_sequences"] = ["SECOND"]
    else:
        body["messages"][0]["content"] = (
            "Call both alpha and beta now. Use the value 1 for alpha and 2 for beta."
        )
        schema = {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        }
        if protocol == "openai":
            body.update(
                tools=[
                    {"type": "function", "function": {"name": name, "parameters": schema}}
                    for name in ("alpha", "beta")
                ],
                tool_choice="required",
                parallel_tool_calls=True,
            )
        else:
            body.update(
                tools=[{"name": name, "input_schema": schema} for name in ("alpha", "beta")],
                tool_choice={"type": "any", "disable_parallel_tool_use": False},
            )
    return body, extra


async def collect(result, protocol, streaming):
    text, reasoning, calls, finish, usage, matched, signature = "", "", {}, None, None, None, None
    if protocol == "openai":
        if streaming:
            async with result:
                async for chunk in result:
                    usage = chunk.usage or usage
                    for choice in chunk.choices:
                        finish = choice.finish_reason or finish
                        text += choice.delta.content or ""
                        reasoning += getattr(choice.delta, "reasoning_content", "") or ""
                        for part in choice.delta.tool_calls or []:
                            call = calls.setdefault(
                                part.index, {"id": "", "name": "", "arguments": ""}
                            )
                            call["id"] += part.id or ""
                            if part.function:
                                call["name"] += part.function.name or ""
                                call["arguments"] += part.function.arguments or ""
        else:
            choice = result.choices[0]
            finish, usage = choice.finish_reason, result.usage
            text = choice.message.content or ""
            reasoning = getattr(choice.message, "reasoning_content", "") or ""
            calls = {
                i: {"id": c.id, "name": c.function.name, "arguments": c.function.arguments}
                for i, c in enumerate(choice.message.tool_calls or [])
            }
        counts = (usage.prompt_tokens, usage.completion_tokens) if usage else (0, 0)
    else:
        blocks = {}
        if streaming:
            async with result:
                async for event in result:
                    if event.type == "content_block_start":
                        blocks[event.index] = event.content_block.model_dump(exclude_none=True)
                        if blocks[event.index]["type"] == "tool_use":
                            blocks[event.index]["arguments"] = ""
                    elif event.type == "content_block_delta":
                        block = blocks[event.index]
                        if event.delta.type == "text_delta":
                            block["text"] += event.delta.text
                        elif event.delta.type == "thinking_delta":
                            block["thinking"] += event.delta.thinking
                        elif event.delta.type == "signature_delta":
                            block["signature"] += event.delta.signature
                        elif event.delta.type == "input_json_delta":
                            block["arguments"] += event.delta.partial_json
                    elif event.type == "message_delta":
                        finish, matched, usage = (
                            event.delta.stop_reason,
                            event.delta.stop_sequence,
                            event.usage,
                        )
                    elif event.type == "error":
                        raise ValueError("Stream error")
        else:
            blocks = {i: b.model_dump(exclude_none=True) for i, b in enumerate(result.content)}
            finish, matched, usage = result.stop_reason, result.stop_sequence, result.usage
        for i, block in blocks.items():
            if block["type"] == "text":
                text += block["text"]
            elif block["type"] == "thinking":
                reasoning += block["thinking"]
                signature = block["signature"]
            elif block["type"] == "tool_use":
                calls[i] = {
                    "id": block["id"],
                    "name": block["name"],
                    "arguments": block.get("arguments", json.dumps(block["input"])),
                }
        counts = (usage.input_tokens, usage.output_tokens) if usage else (0, 0)
    return text, reasoning, calls, finish, counts, matched, signature


def verify(feature, protocol, values):
    text, reasoning, calls, finish, counts, matched, signature = values
    assert counts[0] > 0 and counts[1] > 0, "Missing usage"
    if feature == "structured_output":
        assert json.loads(text) == {"answer": "blue", "detail": {"ok": True}}, "Schema mismatch"
        assert finish in {"stop", "end_turn"}, "Incomplete structured output"
    elif feature == "reasoning":
        assert reasoning.strip() and "391" in text, "Missing reasoning or final answer"
        assert finish in {"stop", "end_turn"}, "Incomplete reasoning output"
        if protocol == "anthropic":
            assert signature == "", "Fabricated signature"
    elif feature == "stop":
        assert "FIRST" in text and "SECOND" not in text and "THIRD" not in text, "Stop not honored"
        if protocol == "anthropic":
            assert finish == "stop_sequence" and matched == "SECOND", "Missing stop evidence"
        else:
            assert finish == "stop", "Unexpected stop finish"
    else:
        assert finish in {"tool_calls", "tool_use"}, "Missing tool finish"
        assert {c["name"] for c in calls.values()} == {"alpha", "beta"}, "Missing tools"
        assert len({c["id"] for c in calls.values()}) == 2, "Invalid call IDs"
        assert {c["name"]: json.loads(c["arguments"]) for c in calls.values()} == {
            "alpha": {"value": 1},
            "beta": {"value": 2},
        }, "Invalid arguments"
    return {
        "input_tokens": counts[0],
        "output_tokens": counts[1],
        "finish": finish,
        "reasoning_present": bool(reasoning),
        "tool_calls": len(calls),
        "stop_sequence_observed": matched is not None,
    }


async def main(options):
    key = (
        (await asyncio.to_thread(Path(options.key_file).read_text)).strip()
        if options.key_file
        else os.getenv("GATEWAY_API_KEY", "local")
    )
    report = {
        "date": datetime.now(UTC).isoformat(),
        "model": options.model,
        "base_url": options.url,
        "results": [],
    }
    for protocol in options.protocol or ("openai", "anthropic"):
        client = (
            AsyncOpenAI(base_url=options.url + "/v1", api_key=key, max_retries=0, timeout=150)
            if protocol == "openai"
            else AsyncAnthropic(base_url=options.url, api_key=key, max_retries=0, timeout=150)
        )
        async with client:
            for feature in options.feature or FEATURES:
                for mode in options.mode or ("json", "stream"):
                    streaming = mode == "stream"
                    row = {"protocol": protocol, "feature": feature, "mode": mode}
                    try:
                        body, extra = request(feature, protocol, options.model)
                        if protocol == "openai" and streaming:
                            body["stream_options"] = {"include_usage": True}
                        api = client.chat.completions if protocol == "openai" else client.messages
                        raw = await api.with_raw_response.create(
                            **body, extra_body=extra, stream=streaming
                        )
                        row["request_id"] = raw.headers.get("x-request-id")
                        result = raw.parse()
                        if inspect.isawaitable(result):
                            result = await result
                        values = await collect(result, protocol, streaming)
                        row["signals"] = {
                            "tool_calls": len(values[2]),
                            "finish": values[3],
                            "input_tokens": values[4][0],
                            "output_tokens": values[4][1],
                            "reasoning_present": bool(values[1]),
                            "stop_sequence_observed": values[5] is not None,
                        }
                        row.update(status="passed", **verify(feature, protocol, values))
                    except Exception as exc:
                        row.update(
                            status="failed",
                            exception_type=type(exc).__name__,
                            http_status=getattr(exc, "status_code", None),
                        )
                        row["request_id"] = getattr(exc, "request_id", row.get("request_id"))
                        safe_checks = {
                            "Missing usage",
                            "Schema mismatch",
                            "Incomplete structured output",
                            "Missing reasoning or final answer",
                            "Incomplete reasoning output",
                            "Fabricated signature",
                            "Stop not honored",
                            "Missing stop evidence",
                            "Unexpected stop finish",
                            "Missing tool finish",
                            "Missing tools",
                            "Invalid call IDs",
                            "Invalid arguments",
                        }
                        if isinstance(exc, AssertionError) and str(exc) in safe_checks:
                            row["failed_check"] = str(exc)
                    report["results"].append(row)
                    await asyncio.to_thread(
                        Path(options.output).write_text, json.dumps(report, indent=2) + "\n"
                    )
                    print(json.dumps(row), flush=True)
    if any(r["status"] != "passed" for r in report["results"]):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--model", default="qwen3.8-27b")
    parser.add_argument("--key-file")
    parser.add_argument("--output", default="live-extended-acceptance.json")
    parser.add_argument("--feature", choices=FEATURES, action="append")
    parser.add_argument("--protocol", choices=("openai", "anthropic"), action="append")
    parser.add_argument("--mode", choices=("json", "stream"), action="append")
    options = parser.parse_args()
    if not options.run:
        parser.error("Use --run to dispatch paid acceptance requests")
    asyncio.run(main(options))
