import json
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any, Literal

from endpoint_gateway.core.models import (
    BlockEnd,
    BlockStart,
    Completion,
    Delta,
    Event,
    Finished,
    Text,
)

type Protocol = Literal["openai", "anthropic"]
type Payload = dict[str, Any]


@dataclass(frozen=True)
class ResponseIdentity:
    id: str
    model: str
    created: int


def usage(finish: Finished, protocol: Protocol) -> Payload:
    if protocol == "anthropic":
        return {"input_tokens": finish.input_tokens, "output_tokens": finish.output_tokens}
    return {
        "prompt_tokens": finish.input_tokens,
        "completion_tokens": finish.output_tokens,
        "total_tokens": finish.input_tokens + finish.output_tokens,
    }


def stop_reason(finish: Finished) -> str:
    return {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}[finish.reason]


def response(result: Completion, identity: ResponseIdentity, protocol: Protocol) -> Payload:
    if protocol == "anthropic":
        blocks = [
            (
                {"type": "text", "text": p.text}
                if isinstance(p, Text)
                else {
                    "type": "tool_use",
                    "id": p.id,
                    "name": p.name,
                    "input": json.loads(p.arguments),
                }
            )
            for p in result.content
        ]
        return {
            "id": identity.id,
            "type": "message",
            "role": "assistant",
            "model": identity.model,
            "content": blocks,
            "stop_reason": stop_reason(result.finish),
            "stop_sequence": None,
            "usage": usage(result.finish, protocol),
        }
    message: Payload = {"role": "assistant", "content": None}
    texts = [p.text for p in result.content if isinstance(p, Text)]
    if texts:
        message["content"] = "".join(texts)
    calls = [
        {"id": p.id, "type": "function", "function": {"name": p.name, "arguments": p.arguments}}
        for p in result.content
        if not isinstance(p, Text)
    ]
    if calls:
        message["tool_calls"] = calls
    return {
        "id": identity.id,
        "object": "chat.completion",
        "created": identity.created,
        "model": identity.model,
        "choices": [{"index": 0, "message": message, "finish_reason": result.finish.reason}],
        "usage": usage(result.finish, protocol),
    }


def sse(payload: Payload, event: str | None = None) -> str:
    prefix = f"event: {event}\n" if event else ""
    return prefix + "data: " + json.dumps(payload, separators=(",", ":")) + "\n\n"


def chunk(identity: ResponseIdentity, delta: Payload, finish: str | None = None) -> Payload:
    return {
        "id": identity.id,
        "object": "chat.completion.chunk",
        "created": identity.created,
        "model": identity.model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


async def openai_stream(
    events: AsyncGenerator[Event, None], identity: ResponseIdentity, include_usage: bool
) -> AsyncGenerator[str, None]:
    tool_index = -1
    in_tool = False
    yield sse(chunk(identity, {"role": "assistant", "content": ""}))
    async with aclosing(events):
        async for event in events:
            if isinstance(event, BlockStart):
                in_tool = event.call is not None
                if event.call is not None:
                    tool_index += 1
                    yield sse(
                        chunk(
                            identity,
                            {
                                "tool_calls": [
                                    {
                                        "index": tool_index,
                                        "id": event.call.id,
                                        "type": "function",
                                        "function": {"name": event.call.name, "arguments": ""},
                                    }
                                ]
                            },
                        )
                    )
            elif isinstance(event, Delta):
                delta = (
                    {"tool_calls": [{"index": tool_index, "function": {"arguments": event.text}}]}
                    if in_tool
                    else {"content": event.text}
                )
                yield sse(chunk(identity, delta))
            elif isinstance(event, Finished):
                yield sse(chunk(identity, {}, event.reason))
                if include_usage:
                    final = chunk(identity, {})
                    final["choices"] = []
                    final["usage"] = usage(event, "openai")
                    yield sse(final)
    yield "data: [DONE]\n\n"


async def anthropic_stream(
    events: AsyncGenerator[Event, None], identity: ResponseIdentity
) -> AsyncGenerator[str, None]:
    start = response(Completion((), Finished("stop")), identity, "anthropic")
    start["stop_reason"] = None
    yield sse({"type": "message_start", "message": start}, "message_start")
    in_tool = False
    async with aclosing(events):
        async for event in events:
            payload: Payload
            match event:
                case BlockStart():
                    in_tool = event.call is not None
                    block = (
                        {"type": "text", "text": ""}
                        if event.call is None
                        else {
                            "type": "tool_use",
                            "id": event.call.id,
                            "name": event.call.name,
                            "input": {},
                        }
                    )
                    payload = {
                        "type": "content_block_start",
                        "index": event.index,
                        "content_block": block,
                    }
                case Delta():
                    delta = (
                        {"type": "input_json_delta", "partial_json": event.text}
                        if in_tool
                        else {"type": "text_delta", "text": event.text}
                    )
                    payload = {"type": "content_block_delta", "index": event.index, "delta": delta}
                case BlockEnd():
                    payload = {"type": "content_block_stop", "index": event.index}
                case Finished():
                    payload = {
                        "type": "message_delta",
                        "delta": {"stop_reason": stop_reason(event), "stop_sequence": None},
                        "usage": usage(event, "anthropic"),
                    }
            yield sse(payload, payload["type"])
    yield sse({"type": "message_stop"}, "message_stop")
