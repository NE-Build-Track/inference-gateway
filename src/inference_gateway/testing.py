"""Deterministic protocol fixtures. No model inference, media fetching or tool execution."""

import asyncio
import base64
import io
import json
import wave
from collections.abc import AsyncGenerator, Mapping
from dataclasses import replace
from typing import Literal

from inference_gateway.core.models import (
    JSON,
    BlockEnd,
    BlockStart,
    Citation,
    Delta,
    Document,
    Event,
    Finished,
    Generate,
    HostedResult,
    Media,
    NamedTool,
    Output,
    Reasoning,
    Text,
    ToolCall,
    ToolResult,
)
from inference_gateway.core.structured import schema_example, validate_value

# One transparent pixel, not generated image content.
PIXEL = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def silence(fmt: str) -> str:
    pcm = b"\0\0" * 240
    if fmt == "pcm16":
        return base64.b64encode(pcm).decode()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(pcm)
    return base64.b64encode(buffer.getvalue()).decode()


class FakeBackend:
    """Echo text, deterministic schema examples, and synthetic feature fixtures.

    Sampling values are carried in the command; only stop sequences affect this
    non-probabilistic backend. Tool and structured fixtures can be injected.
    """

    def __init__(
        self,
        tool_arguments: Mapping[str, dict[str, JSON]] | None = None,
        *,
        structured_output: dict[str, JSON] | None = None,
    ) -> None:
        self._arguments = dict(tool_arguments or {})
        self._structured = structured_output

    def blocks(self, request: Generate) -> tuple[list[Output], Finished]:
        last = request.messages[-1]
        results = [part for part in last.content if isinstance(part, ToolResult)]
        prompt = " ".join(p.text for p in last.content if isinstance(p, Text))
        blocks: list[Output] = []
        if request.reasoning:
            blocks.append(Reasoning("Synthetic reasoning fixture.", "fake-signature"))
        use_tool = request.tool_choice != "none" and (not results or request.tool_choice != "auto")
        reason: Literal["stop", "length", "tool_calls"] = "stop"
        number = sum(isinstance(p, ToolCall) for m in request.messages for p in m.content)
        hosted = (
            request.hosted_search
            and use_tool
            and (
                not isinstance(request.tool_choice, NamedTool)
                or request.tool_choice.name == "web_search"
            )
        )
        if hosted:
            call_id = f"srv_fake_{number}"
            source = Citation(
                "Synthetic search fixture", "Synthetic search result.", "https://example.com/fake"
            )
            blocks.extend(
                [
                    ToolCall(call_id, "web_search", '{"query":"synthetic fixture"}', True),
                    HostedResult(call_id, (source,)),
                ]
            )
        tools = [
            t
            for t in request.tools
            if not isinstance(request.tool_choice, NamedTool) or t.name == request.tool_choice.name
        ]
        if tools and use_tool and not hosted:
            selected = tools if request.parallel_tools else tools[:1]
            blocks.extend(
                ToolCall(
                    f"call_fake_{number + i}",
                    t.name,
                    json.dumps(self._arguments.get(t.name, {}), separators=(",", ":")),
                )
                for i, t in enumerate(selected)
            )
            reason = "tool_calls"
        else:
            citations = list(request.sources)
            docs = [p for m in request.messages for p in m.content if isinstance(p, Document)]
            citations.extend(
                Citation(d.title, d.text, document_index=i, end=len(d.text))
                for i, d in enumerate(docs)
                if d.cite
            )
            if hosted:
                citations.append(source)
            text = (
                "Tool result: " + " ".join(r.text for r in results)
                if results
                else "Echo: " + prompt
            )
            media = [p.kind for p in last.content if isinstance(p, Media)]
            if media:
                text += " [fake received: " + ", ".join(media) + "]"
            if request.output_format is not None:
                schema = request.output_format.schema
                value = (
                    self._structured
                    if self._structured is not None
                    else schema_example(schema)
                    if schema
                    else {}
                )
                if schema:
                    validate_value(value, schema)
                text = json.dumps(value, separators=(",", ":"))
            if citations:
                text = " ".join(c.text for c in citations)
                offset = 0
                located = []
                for c in citations:
                    located.append(
                        replace(c, start=offset, end=offset + len(c.text)) if c.url else c
                    )
                    offset += len(c.text) + 1
                citations = located
            stop_sequence = None
            stops = [
                (text.index(s), i, s) for i, s in enumerate(request.sampling.stop) if s in text
            ]
            if stops:
                offset, _, stop_sequence = min(stops)
                text = text[:offset]
            if len(text) > request.max_tokens:
                text, reason, stop_sequence = text[: request.max_tokens], "length", None
            # Citations describe complete quoted spans; omit those cut by stop/length.
            if stops or reason == "length":
                citations = []
            if request.output_text:
                blocks.append(Text(text, tuple(citations)))
            for kind in request.output_media:
                blocks.append(
                    Media(
                        kind,
                        PIXEL if kind == "image" else silence(request.audio_format),
                        "image/png" if kind == "image" else "audio/" + request.audio_format,
                    )
                )
            return blocks, Finished(
                reason,
                0,
                sum(len(p.text) for p in blocks if isinstance(p, (Text, Reasoning))),
                stop_sequence=stop_sequence,
            )
        return blocks, Finished(
            reason,
            0,
            sum(
                len(p.arguments)
                if isinstance(p, ToolCall)
                else len(p.text)
                if isinstance(p, Reasoning)
                else 0
                for p in blocks
            ),
        )

    async def stream(self, request: Generate) -> AsyncGenerator[Event, None]:
        blocks, finish = self.blocks(request)
        index = 0
        while index < len(blocks):
            block = blocks[index]
            # Open every adjacent client call before round-robin deltas.
            if isinstance(block, ToolCall) and not block.hosted and request.parallel_tools:
                group = []
                while index < len(blocks) and isinstance(blocks[index], ToolCall):
                    call = blocks[index]
                    assert isinstance(call, ToolCall)
                    group.append((index, call.arguments))
                    yield BlockStart(index, replace(call, arguments=""))
                    index += 1
                for offset in range(0, max(len(t) for _, t in group), 4):
                    for i, text in group:
                        if offset < len(text):
                            await asyncio.sleep(0)
                            yield Delta(i, text[offset : offset + 4])
                for i, _ in group:
                    yield BlockEnd(i)
                continue
            if isinstance(block, ToolCall):
                yield BlockStart(index, replace(block, arguments=""))
                text = block.arguments
            elif isinstance(block, (Text, Reasoning)):
                yield BlockStart(index, block=replace(block, text=""))
                text = block.text
            else:
                yield BlockStart(index, block=block)
                text = ""
            for offset in range(0, len(text), 4):
                await asyncio.sleep(0)
                yield Delta(index, text[offset : offset + 4])
            yield BlockEnd(index)
            index += 1
        yield finish
