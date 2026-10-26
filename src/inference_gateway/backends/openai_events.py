"""Runner chunks to bounded reasoning, text and independently indexed tool blocks."""

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from inference_gateway.backends.stop_sequences import StopFilter
from inference_gateway.core.backend import BackendError, UnsupportedResponse
from inference_gateway.core.models import (
    BlockEnd,
    BlockStart,
    Delta,
    Event,
    Finished,
    Reasoning,
    ToolCall,
)


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
    audio: Any = None
    annotations: Any = None
    images: Any = None
    gateway_blocks: Any = None
    refusal: str | None = None


class Choice(Wire):
    index: Literal[0]
    delta: MessageDelta
    finish_reason: Literal["stop", "length", "tool_calls"] | None = None
    stop_reason: str | int | None = None


class PromptDetails(Wire):
    cached_tokens: int = Field(default=0, ge=0)


class Usage(Wire):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    prompt_tokens_details: PromptDetails | None = None


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
    block_index: int | None = None


class CompletionDecoder:
    def __init__(
        self,
        max_output_bytes: int,
        *,
        allow_reasoning: bool = False,
        parallel_tools: bool = False,
        stop_sequences: tuple[str, ...] = (),
    ) -> None:
        self._allow_reasoning = allow_reasoning
        self._parallel = parallel_tools
        self._reasoning_open = False
        self._reasoning_seen = False
        self._content_seen = False
        self._stop_filter = StopFilter(stop_sequences)
        self._reported_stop: str | None = None
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
            if any(
                v is not None
                for v in (delta.audio, delta.annotations, delta.images, delta.gateway_blocks)
            ):
                raise UnsupportedResponse("Runner emitted unsupported output blocks")
            if delta.refusal:
                raise UnsupportedResponse("Runner refused the requested output")
            if (
                delta.reasoning
                and delta.reasoning_content
                and delta.reasoning != delta.reasoning_content
            ):
                raise UnsupportedResponse("Conflicting reasoning fields")
            reasoning = delta.reasoning_content or delta.reasoning
            if reasoning:
                out.extend(self._reasoning(reasoning))
            if delta.content:
                if self._tools:
                    raise UnsupportedResponse("Text after tool output is unsupported")
                out.extend(self._close_reasoning())
                self._content_seen = True
                self._count(delta.content)
                text = self._stop_filter.feed(delta.content)
                if not self._text_open:
                    out.append(BlockStart(self._next_block))
                    self._text_open = True
                if text:
                    out.append(Delta(self._next_block, text))
            for fragment in delta.tool_calls or []:
                out.extend(self._tool(fragment))
            if choice.stop_reason is not None:
                if isinstance(choice.stop_reason, str):
                    if choice.stop_reason not in self._stop_filter.sequences:
                        raise UnsupportedResponse("Runner reported an unrequested stop sequence")
                    if (
                        self._reported_stop is not None
                        and self._reported_stop != choice.stop_reason
                    ):
                        raise UnsupportedResponse("Runner changed stop sequence")
                    self._reported_stop = choice.stop_reason
            if choice.finish_reason is not None:
                self._finish = choice.finish_reason
        return out

    def _reasoning(self, value: str) -> list[Event]:
        if not self._allow_reasoning or self._content_seen or self._tools:
            raise UnsupportedResponse("Reasoning is disabled or arrived after final output")
        self._count(value)
        out: list[Event] = []
        if not self._reasoning_open:
            out.append(BlockStart(self._next_block, block=Reasoning("")))
            self._reasoning_open = self._reasoning_seen = True
        out.append(Delta(self._next_block, value))
        return out

    def _close_reasoning(self) -> list[Event]:
        if not self._reasoning_open:
            return []
        out: list[Event] = [BlockEnd(self._next_block)]
        self._next_block += 1
        self._reasoning_open = False
        return out

    def _count(self, value: str) -> None:
        self._bytes += len(value.encode("utf-8"))
        if self._bytes > self._limit:
            raise BackendError("Runner output exceeds configured limit")

    def _tool(self, fragment: CallDelta) -> list[Event]:
        if self._stop_filter.sequences:
            raise UnsupportedResponse("Tool output cannot be combined with stop controls")
        out: list[Event] = self._close_reasoning()
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
        if (
            (self._parallel or fragment.index == self._active_tool)
            and parts.id
            and parts.name
            and parts.fragments
        ):
            if not parts.started:
                parts.block_index = self._next_block
                out.append(BlockStart(parts.block_index, ToolCall(parts.id, parts.name, "")))
                if self._parallel:
                    self._next_block += 1
                parts.started = True
            assert parts.block_index is not None
            # Arguments may precede identity fields in fragmented upstream chunks.
            out.extend(
                Delta(parts.block_index, value) for value in parts.fragments[parts.emitted :]
            )
            parts.emitted = len(parts.fragments)
        return out

    def _end(self) -> list[Event]:
        if self._finish is None or self._usage is None:
            raise BackendError("Runner omitted finish reason or token usage")
        if self._tools and self._finish not in {"tool_calls", "stop"}:
            raise BackendError("Incomplete runner tool calls")
        if not self._tools and self._finish == "tool_calls":
            raise BackendError("Runner finished with no tool calls")
        if (
            self._reasoning_seen
            and not self._content_seen
            and not self._tools
            and self._finish == "stop"
        ):
            raise UnsupportedResponse("Reasoning ended without final output")
        if (
            self._reported_stop
            and self._stop_filter.matched
            and self._reported_stop != self._stop_filter.matched
        ):
            raise UnsupportedResponse("Conflicting stop evidence")
        matched = self._stop_filter.matched or self._reported_stop
        if matched and self._finish != "stop":
            raise UnsupportedResponse("Stop sequence contradicts runner finish")
        out: list[Event] = self._close_reasoning()
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
                parts.block_index = self._next_block
                out.append(BlockStart(parts.block_index, ToolCall(parts.id, parts.name, "")))
                out.extend(Delta(parts.block_index, part) for part in parts.fragments)
                if self._parallel:
                    self._next_block += 1
            assert parts.block_index is not None
            out.append(BlockEnd(parts.block_index))
            if not self._parallel:
                self._next_block += 1
        if self._text_open:
            tail = self._stop_filter.finish()
            if tail:
                out.append(Delta(self._next_block, tail))
            out.append(BlockEnd(self._next_block))
        if not self._tools and not self._text_open and not self._reasoning_seen:
            out.extend([BlockStart(0), Delta(0, ""), BlockEnd(0)])
        reason = "tool_calls" if self._tools else self._finish
        cached = (
            self._usage.prompt_tokens_details.cached_tokens
            if self._usage.prompt_tokens_details
            else 0
        )
        if cached > self._usage.prompt_tokens:
            raise BackendError("Invalid cached token count")
        out.append(
            Finished(
                reason, self._usage.prompt_tokens, self._usage.completion_tokens, cached, matched
            )
        )
        self.done = True
        return out
