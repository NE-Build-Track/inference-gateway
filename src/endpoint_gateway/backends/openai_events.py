"""Runner chunks → sequential gateway blocks, with bounded interleaved tool buffering."""

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from endpoint_gateway.core.backend import BackendError
from endpoint_gateway.core.models import BlockEnd, BlockStart, Delta, Event, Finished, ToolCall


def reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON value")


class Wire(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class FunctionDelta(Wire):
    name: str | None = None
    arguments: str | None = None


class CallDelta(Wire):
    index: int = Field(ge=0)
    id: str | None = None
    type: Literal["function"] | None = None
    function: FunctionDelta | None = None


class MessageDelta(Wire):
    role: Literal["assistant"] | None = None
    content: str | None = None
    tool_calls: list[CallDelta] | None = None
    reasoning: str | None = None
    reasoning_content: str | None = None


class Choice(Wire):
    index: Literal[0]
    delta: MessageDelta
    finish_reason: Literal["stop", "length", "tool_calls"] | None = None


class Usage(Wire):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)


class Chunk(Wire):
    choices: list[Choice] = Field(max_length=1)
    usage: Usage | None = None
    error: Any = None


@dataclass
class ToolParts:
    id: str = ""
    name: str = ""
    fragments: list[str] = field(default_factory=list)
    started: bool = False
    emitted: int = 0


class CompletionDecoder:
    def __init__(self, max_output_bytes: int) -> None:
        self._limit = max_output_bytes
        self._bytes = 0
        self._text_open = False
        self._next_block = 0
        self._tools: dict[int, ToolParts] = {}
        self._active_tool: int | None = None
        self._finish: Literal["stop", "length", "tool_calls"] | None = None
        self._usage: Usage | None = None
        self.done = False

    def feed(self, data: str) -> list[Event]:
        if self.done:
            raise BackendError("Data after runner terminal marker")
        if data == "[DONE]":
            return self._end()
        try:
            chunk = Chunk.model_validate(json.loads(data, parse_constant=reject_constant))
        except (ValueError, ValidationError) as exc:
            raise BackendError("Invalid runner completion chunk") from exc
        if chunk.error is not None:
            raise BackendError("Runner reported an error")
        if chunk.usage is not None:
            self._usage = chunk.usage
        out: list[Event] = []
        for choice in chunk.choices:
            if self._finish is not None:
                raise BackendError("Choice after runner finish")
            delta = choice.delta
            if delta.reasoning or delta.reasoning_content:
                raise BackendError("Runner emitted unsupported reasoning content")
            if delta.content:
                if self._tools:
                    raise BackendError("Text after tool output is unsupported")
                self._count(delta.content)
                if not self._text_open:
                    out.append(BlockStart(self._next_block))
                    self._text_open = True
                out.append(Delta(self._next_block, delta.content))
            for fragment in delta.tool_calls or []:
                out.extend(self._tool(fragment))
            if choice.finish_reason is not None:
                self._finish = choice.finish_reason
        return out

    def _count(self, value: str) -> None:
        self._bytes += len(value.encode("utf-8"))
        if self._bytes > self._limit:
            raise BackendError("Runner output exceeds configured limit")

    def _tool(self, fragment: CallDelta) -> list[Event]:
        out: list[Event] = []
        if self._text_open:
            out.append(BlockEnd(self._next_block))
            self._text_open = False
            self._next_block += 1
        if fragment.index not in self._tools:
            if len(self._tools) >= 128:
                raise BackendError("Too many runner tool calls")
            self._tools[fragment.index] = ToolParts()
            if self._active_tool is None:
                self._active_tool = fragment.index
        parts = self._tools[fragment.index]
        if fragment.id:
            if parts.id and parts.id != fragment.id:
                raise BackendError("Runner changed a tool call ID")
            self._count(fragment.id)
            parts.id = fragment.id
        function = fragment.function
        if function is not None:
            if function.name:
                if parts.started and function.name != parts.name:
                    raise BackendError("Runner changed a tool name")
                # Names may be fragmented before the first arguments, as IDs become available.
                if not parts.name or function.name != parts.name:
                    self._count(function.name)
                    parts.name += function.name
            if function.arguments:
                self._count(function.arguments)
                parts.fragments.append(function.arguments)
        if fragment.index == self._active_tool and parts.id and parts.name and parts.fragments:
            if not parts.started:
                out.append(BlockStart(self._next_block, ToolCall(parts.id, parts.name, "")))
                parts.started = True
            # Arguments may precede identity fields in fragmented upstream chunks.
            out.extend(Delta(self._next_block, value) for value in parts.fragments[parts.emitted :])
            parts.emitted = len(parts.fragments)
        return out

    def _end(self) -> list[Event]:
        if self._finish is None or self._usage is None:
            raise BackendError("Runner omitted finish reason or token usage")
        if self._tools and self._finish != "tool_calls":
            raise BackendError("Incomplete runner tool calls")
        if not self._tools and self._finish == "tool_calls":
            raise BackendError("Runner finished with no tool calls")
        out: list[Event] = []
        ids: set[str] = set()
        for parts in self._tools.values():
            if not parts.id or not parts.name or parts.id in ids:
                raise BackendError("Invalid runner tool identity")
            ids.add(parts.id)
            try:
                arguments = json.loads("".join(parts.fragments), parse_constant=reject_constant)
            except ValueError as exc:
                raise BackendError("Invalid runner tool arguments") from exc
            if not isinstance(arguments, dict):
                raise BackendError("Runner tool arguments must be an object")
            if not parts.started:
                out.append(BlockStart(self._next_block, ToolCall(parts.id, parts.name, "")))
                out.extend(Delta(self._next_block, part) for part in parts.fragments)
            out.append(BlockEnd(self._next_block))
            self._next_block += 1
        if self._text_open:
            out.append(BlockEnd(self._next_block))
        if not self._tools and not self._text_open:
            out.extend([BlockStart(0), Delta(0, ""), BlockEnd(0)])
        out.append(Finished(self._finish, self._usage.prompt_tokens, self._usage.completion_tokens))
        self.done = True
        return out
