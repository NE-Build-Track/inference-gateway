"""Deterministic fake backend, also usable by consumers in their own tests."""

import asyncio
import json
from collections.abc import AsyncGenerator, Mapping
from typing import Literal

from endpoint_gateway.core.models import (
    JSON,
    BlockEnd,
    BlockStart,
    Delta,
    Event,
    Finished,
    Generate,
    NamedTool,
    Text,
    ToolCall,
    ToolResult,
)


class FakeBackend:
    """Echo text; select the first offered tool unless disabled or results arrive.

    Tool arguments default to {}. Supply tool_arguments for schema-specific
    fixtures. This fake never infers arguments or executes tools.
    """

    def __init__(self, tool_arguments: Mapping[str, dict[str, JSON]] | None = None) -> None:
        self._arguments = dict(tool_arguments or {})

    async def stream(self, request: Generate) -> AsyncGenerator[Event, None]:
        last = request.messages[-1]
        results = [part for part in last.content if isinstance(part, ToolResult)]
        use_tool = (
            request.tools
            and request.tool_choice != "none"
            and (not results or request.tool_choice not in {"auto", "none"})
        )
        reason: Literal["stop", "length", "tool_calls"]
        if use_tool:
            tool = next(
                (
                    t
                    for t in request.tools
                    if isinstance(request.tool_choice, NamedTool)
                    and t.name == request.tool_choice.name
                ),
                request.tools[0],
            )
            call_number = sum(isinstance(p, ToolCall) for m in request.messages for p in m.content)
            yield BlockStart(0, ToolCall(f"call_fake_{call_number}", tool.name, ""))
            text = json.dumps(self._arguments.get(tool.name, {}), separators=(",", ":"))
            reason = "tool_calls"
        else:
            yield BlockStart(0)
            prompt = " ".join(p.text for p in last.content if isinstance(p, Text))
            text = (
                "Tool result: " + " ".join(r.text for r in results)
                if results
                else "Echo: " + prompt
            )
            reason = "stop"
        # Fake token accounting is character-based and is never billing data.
        if reason == "stop" and len(text) > request.max_tokens:
            text, reason = text[: request.max_tokens], "length"
        for offset in range(0, len(text), 4):
            await asyncio.sleep(0)
            yield Delta(0, text[offset : offset + 4])
        yield BlockEnd(0)
        yield Finished(reason, 0, len(text))
