import json
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass, replace
from typing import Any, Literal

from inference_gateway.core.models import (
    BlockEnd,
    BlockStart,
    Citation,
    Completion,
    Delta,
    Event,
    Finished,
    HostedResult,
    Media,
    Output,
    Reasoning,
    Text,
    ToolCall,
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
        return {
            "input_tokens": finish.input_tokens - finish.cached_input_tokens,
            "output_tokens": finish.output_tokens,
            **(
                {"cache_read_input_tokens": finish.cached_input_tokens}
                if finish.cached_input_tokens
                else {}
            ),
        }
    return {
        "prompt_tokens": finish.input_tokens,
        "completion_tokens": finish.output_tokens,
        "total_tokens": finish.input_tokens + finish.output_tokens,
        **(
            {"prompt_tokens_details": {"cached_tokens": finish.cached_input_tokens}}
            if finish.cached_input_tokens
            else {}
        ),
    }


def stop_reason(finish: Finished) -> str:
    if finish.stop_sequence is not None:
        return "stop_sequence"
    return {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}[finish.reason]


def citation_wire(c: Citation) -> Payload:
    if c.document_index is not None:
        return {
            "type": "char_location",
            "cited_text": c.text,
            "document_index": c.document_index,
            "document_title": c.title,
            "start_char_index": c.start,
            "end_char_index": c.end,
        }
    return {
        "type": "web_search_result_location",
        "cited_text": c.text,
        "title": c.title,
        "url": c.url,
        "encrypted_index": c.token,
    }


def annotations(text: Text, offset: int = 0) -> list[Payload]:
    return [
        {
            "type": "url_citation",
            "url_citation": {
                "url": c.url,
                "title": c.title,
                "start_index": offset + c.start,
                "end_index": offset + c.end,
            },
        }
        for c in text.citations
        if c.url is not None
    ]


def block_wire(p: Output) -> Payload:
    if isinstance(p, Text):
        return {
            "type": "text",
            "text": p.text,
            **({"citations": [citation_wire(c) for c in p.citations]} if p.citations else {}),
        }
    if isinstance(p, Reasoning):
        return {"type": "thinking", "thinking": p.text, "signature": p.signature}
    if isinstance(p, ToolCall):
        return {
            "type": "server_tool_use" if p.hosted else "tool_use",
            "id": p.id,
            "name": p.name,
            "input": json.loads(p.arguments),
        }
    if isinstance(p, HostedResult):
        return {
            "type": "web_search_tool_result",
            "tool_use_id": p.call_id,
            "content": [
                {
                    "type": "web_search_result",
                    "url": c.url,
                    "title": c.title,
                    "encrypted_content": c.token,
                }
                for c in p.results
            ],
        }
    return {"type": "gateway_media", "kind": p.kind, "data": p.data, "media_type": p.media_type}


def audio_wire(p: Media, identity: ResponseIdentity, index: int) -> Payload:
    return {
        "id": identity.id + f"_audio_{index}",
        "data": p.data,
        "expires_at": identity.created + 3600,
        "transcript": "",
    }


def response(result: Completion, identity: ResponseIdentity, protocol: Protocol) -> Payload:
    if protocol == "anthropic":
        return {
            "id": identity.id,
            "type": "message",
            "role": "assistant",
            "model": identity.model,
            "content": [block_wire(p) for p in result.content],
            "stop_reason": stop_reason(result.finish),
            "stop_sequence": result.finish.stop_sequence,
            "usage": usage(result.finish, protocol),
        }
    message: Payload = {"role": "assistant", "content": None}
    texts = [p.text for p in result.content if isinstance(p, Text)]
    if texts:
        message["content"] = "".join(texts)
    calls = [
        {"id": p.id, "type": "function", "function": {"name": p.name, "arguments": p.arguments}}
        for p in result.content
        if isinstance(p, ToolCall) and not p.hosted
    ]
    if calls:
        message["tool_calls"] = calls
    reasoning = [p.text for p in result.content if isinstance(p, Reasoning)]
    if reasoning:
        message["reasoning_content"] = "".join(reasoning)
    extras = []
    offset = 0
    for index, p in enumerate(result.content):
        if isinstance(p, Text):
            if annotations(p, offset):
                message.setdefault("annotations", []).extend(annotations(p, offset))
            docs = [citation_wire(c) for c in p.citations if c.document_index is not None]
            if docs:
                message.setdefault("gateway_citations", []).extend(docs)
            offset += len(p.text)
        elif isinstance(p, Media) and p.kind == "audio":
            message["audio"] = audio_wire(p, identity, index)
        elif isinstance(p, (HostedResult, Media)) or isinstance(p, ToolCall) and p.hosted:
            extras.append({"index": index, "block": block_wire(p)})
    if extras:
        message["gateway_blocks"] = extras
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
    starts: dict[int, BlockStart] = {}
    tools: dict[int, int] = {}
    hosted_chunks: dict[int, list[str]] = {}
    text_lengths: dict[int, int] = {}
    yield sse(chunk(identity, {"role": "assistant", "content": ""}))
    async with aclosing(events):
        async for event in events:
            if isinstance(event, BlockStart):
                starts[event.index] = event
                if event.call and not event.call.hosted:
                    tools[event.index] = len(tools)
                    yield sse(
                        chunk(
                            identity,
                            {
                                "tool_calls": [
                                    {
                                        "index": tools[event.index],
                                        "id": event.call.id,
                                        "type": "function",
                                        "function": {"name": event.call.name, "arguments": ""},
                                    }
                                ]
                            },
                        )
                    )
                elif event.call:
                    hosted_chunks[event.index] = []
                elif event.block is None or isinstance(event.block, Text):
                    text_lengths[event.index] = 0
            elif isinstance(event, Delta):
                start = starts[event.index]
                if start.call and start.call.hosted:
                    hosted_chunks[event.index].append(event.text)
                    continue
                delta: Payload
                if start.call:
                    delta = {
                        "tool_calls": [
                            {"index": tools[event.index], "function": {"arguments": event.text}}
                        ]
                    }
                elif isinstance(start.block, Reasoning):
                    delta = {"reasoning_content": event.text}
                else:
                    delta = {"content": event.text}
                    text_lengths[event.index] += len(event.text)
                yield sse(chunk(identity, delta))
            elif isinstance(event, BlockEnd):
                start = starts.pop(event.index)
                if start.call and start.call.hosted:
                    block = block_wire(
                        replace(start.call, arguments="".join(hosted_chunks.pop(event.index)))
                    )
                    yield sse(
                        chunk(
                            identity, {"gateway_blocks": [{"index": event.index, "block": block}]}
                        )
                    )
                elif isinstance(start.block, Media) and start.block.kind == "audio":
                    yield sse(
                        chunk(identity, {"audio": audio_wire(start.block, identity, event.index)})
                    )
                elif isinstance(start.block, (HostedResult, Media)):
                    yield sse(
                        chunk(
                            identity,
                            {
                                "gateway_blocks": [
                                    {"index": event.index, "block": block_wire(start.block)}
                                ]
                            },
                        )
                    )
                elif isinstance(start.block, Text):
                    offset = sum(n for i, n in text_lengths.items() if i < event.index)
                    values = annotations(start.block, offset)
                    if values:
                        yield sse(chunk(identity, {"annotations": values}))
                    docs = [
                        citation_wire(c)
                        for c in start.block.citations
                        if c.document_index is not None
                    ]
                    if docs:
                        yield sse(chunk(identity, {"gateway_citations": docs}))
            elif isinstance(event, Finished):
                yield sse(chunk(identity, {}, event.reason))
                if include_usage:
                    final = chunk(identity, {})
                    final["choices"], final["usage"] = [], usage(event, "openai")
                    yield sse(final)
    yield "data: [DONE]\n\n"


async def anthropic_stream(
    events: AsyncGenerator[Event, None], identity: ResponseIdentity
) -> AsyncGenerator[str, None]:
    start = response(Completion((), Finished("stop")), identity, "anthropic")
    start["stop_reason"] = None
    yield sse({"type": "message_start", "message": start}, "message_start")
    starts: dict[int, BlockStart] = {}
    async with aclosing(events):
        async for event in events:
            payload: Payload
            match event:
                case BlockStart():
                    starts[event.index] = event
                    block: Payload
                    if event.call:
                        block = {
                            "type": "server_tool_use" if event.call.hosted else "tool_use",
                            "id": event.call.id,
                            "name": event.call.name,
                            "input": {},
                        }
                    else:
                        block = block_wire(event.block or Text(""))
                        if isinstance(event.block, Reasoning):
                            block["signature"] = ""
                        if isinstance(event.block, Text):
                            block.pop("citations", None)
                    payload = {
                        "type": "content_block_start",
                        "index": event.index,
                        "content_block": block,
                    }
                case Delta():
                    current = starts[event.index]
                    delta = (
                        {"type": "input_json_delta", "partial_json": event.text}
                        if current.call
                        else {"type": "thinking_delta", "thinking": event.text}
                        if isinstance(current.block, Reasoning)
                        else {"type": "text_delta", "text": event.text}
                    )
                    payload = {"type": "content_block_delta", "index": event.index, "delta": delta}
                case BlockEnd():
                    current = starts.pop(event.index)
                    tails: list[Payload] = []
                    if isinstance(current.block, Reasoning):
                        tails.append(
                            {"type": "signature_delta", "signature": current.block.signature}
                        )
                    if isinstance(current.block, Text):
                        tails.extend(
                            {"type": "citations_delta", "citation": citation_wire(c)}
                            for c in current.block.citations
                        )
                    for tail in tails:
                        yield sse(
                            {"type": "content_block_delta", "index": event.index, "delta": tail},
                            "content_block_delta",
                        )
                    payload = {"type": "content_block_stop", "index": event.index}
                case Finished():
                    payload = {
                        "type": "message_delta",
                        "delta": {
                            "stop_reason": stop_reason(event),
                            "stop_sequence": event.stop_sequence,
                        },
                        "usage": usage(event, "anthropic"),
                    }
            yield sse(payload, payload["type"])
    yield sse({"type": "message_stop"}, "message_stop")
