"""Supported wire contracts. Unknown fields fail instead of disappearing."""

import json
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from inference_gateway.adapters.feature_schemas import (
    Annotation,
    AudioInput,
    AudioOutput,
    CharCitation,
    DocumentBlock,
    GatewayHistoryBlock,
    GatewayOptions,
    HostedSearch,
    ImageBlock,
    ImageURLBlock,
    MediaOutputBlock,
    OutputConfig,
    ResponseFormat,
    SearchResults,
    ServerToolUse,
    ThinkingBlock,
    ThinkingDisabled,
    ThinkingEnabled,
    WebCitation,
    WireModel,
)
from inference_gateway.core.models import JSON
from inference_gateway.core.structured import reject_nonfinite


class TextBlock(WireModel):
    type: Literal["text"] = "text"
    text: str
    citations: list[CharCitation | WebCitation] | None = None


def object_schema() -> dict[str, JSON]:
    return {"type": "object"}


class Function(WireModel):
    name: str = Field(min_length=1)
    description: str = ""
    parameters: dict[str, JSON] = Field(default_factory=object_schema)
    strict: bool = False


class OpenAITool(WireModel):
    type: Literal["function"] = "function"
    function: Function


class CallFunction(WireModel):
    name: str = Field(min_length=1)
    arguments: str

    @model_validator(mode="after")
    def valid_json(self) -> Self:
        if not isinstance(json.loads(self.arguments, parse_constant=reject_nonfinite), dict):
            raise ValueError("Tool arguments must encode a JSON object")
        return self


class OpenAICall(WireModel):
    id: str = Field(min_length=1)
    type: Literal["function"] = "function"
    function: CallFunction


class OpenAIMessage(WireModel):
    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str | list[TextBlock | ImageURLBlock | AudioInput] | None = None
    tool_calls: list[OpenAICall] = Field(default_factory=list)
    tool_call_id: str | None = None
    reasoning_content: str | None = None
    annotations: list[Annotation] | None = None
    gateway_blocks: list[GatewayHistoryBlock] = Field(default_factory=list)
    gateway_citations: list[CharCitation] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_role(self) -> Self:
        if (
            self.reasoning_content is not None
            or self.annotations
            or self.gateway_blocks
            or self.gateway_citations
        ) and self.role != "assistant":
            raise ValueError("Reasoning and annotations require assistant role")
        if (
            isinstance(self.content, list)
            and any(not isinstance(p, TextBlock) for p in self.content)
            and self.role != "user"
        ):
            raise ValueError("Media requires user role")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("Only assistant messages may contain tool_calls")
        if (self.role == "tool") != (self.tool_call_id is not None):
            raise ValueError("tool_call_id is required only for tool messages")
        if self.annotations and not isinstance(self.content, str):
            raise ValueError("Annotations require string content")
        indices = [entry.index for entry in self.gateway_blocks]
        if indices != sorted(set(indices)):
            raise ValueError("Gateway block indices must be unique and ordered")
        if self.content is None and not self.tool_calls and not self.gateway_blocks:
            raise ValueError("Message requires content or tool_calls")
        return self


class FunctionName(WireModel):
    name: str = Field(min_length=1)


class OpenAIChoice(WireModel):
    type: Literal["function"]
    function: FunctionName


class StreamOptions(WireModel):
    include_usage: bool = False


class OpenAIRequest(WireModel):
    model: str = Field(min_length=1)
    messages: list[OpenAIMessage] = Field(min_length=1)
    stream: bool = False
    tools: list[OpenAITool] = Field(default_factory=list)
    tool_choice: Literal["auto", "none", "required"] | OpenAIChoice = "auto"
    max_tokens: int | None = Field(default=None, gt=0)
    max_completion_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2)
    stream_options: StreamOptions | None = None
    n: Literal[1] = 1
    top_p: float | None = Field(default=None, ge=0, le=1)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    seed: int | None = None
    stop: str | list[str] | None = None
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = (
        None
    )
    response_format: ResponseFormat | None = None
    parallel_tool_calls: bool = False
    modalities: list[Literal["text", "audio"]] | None = None
    audio: AudioOutput | None = None
    gateway: GatewayOptions = Field(default_factory=GatewayOptions)

    @model_validator(mode="after")
    def token_limit(self) -> Self:
        if self.max_tokens is not None and self.max_completion_tokens is not None:
            raise ValueError("Specify one token limit")
        if self.stream_options is not None and not self.stream:
            raise ValueError("stream_options requires stream=true")
        if (self.audio is not None) != (self.modalities is not None and "audio" in self.modalities):
            raise ValueError("Audio options and audio modality must be specified together")
        if self.modalities is not None and (
            not self.modalities or len(set(self.modalities)) != len(self.modalities)
        ):
            raise ValueError("Invalid modalities")
        return self


class ToolUse(WireModel):
    type: Literal["tool_use"]
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    input: dict[str, JSON]
    caller: None = None
    toolset_name: None = None


class ResultBlock(WireModel):
    type: Literal["tool_result"]
    tool_use_id: str = Field(min_length=1)
    content: str | list[TextBlock] = ""
    is_error: bool = False


type AnthropicBlock = Annotated[
    TextBlock
    | ToolUse
    | ResultBlock
    | ImageBlock
    | AudioInput
    | ThinkingBlock
    | DocumentBlock
    | ServerToolUse
    | SearchResults
    | MediaOutputBlock,
    Field(discriminator="type"),
]


class AnthropicMessage(WireModel):
    role: Literal["user", "assistant"]
    content: str | list[AnthropicBlock]

    @model_validator(mode="after")
    def valid_role(self) -> Self:
        if isinstance(self.content, list):
            for block in self.content:
                if (
                    isinstance(
                        block,
                        (ToolUse, ThinkingBlock, ServerToolUse, SearchResults, MediaOutputBlock),
                    )
                    and self.role != "assistant"
                ):
                    raise ValueError("tool_use requires assistant role")
                if (
                    isinstance(block, (ImageBlock, AudioInput, DocumentBlock))
                    and self.role != "user"
                ):
                    raise ValueError("Media and documents require user role")
                if isinstance(block, ResultBlock) and self.role != "user":
                    raise ValueError("tool_result requires user role")
        return self


class AnthropicTool(WireModel):
    name: str = Field(min_length=1)
    description: str = ""
    input_schema: dict[str, JSON]


class AnthropicChoice(WireModel):
    type: Literal["auto", "none", "any", "tool"]
    name: str | None = None
    disable_parallel_tool_use: bool = False

    @model_validator(mode="after")
    def named_tool(self) -> Self:
        if (self.type == "tool") != (self.name is not None):
            raise ValueError("name is required only for tool choice type=tool")
        return self


class AnthropicRequest(WireModel):
    model: str = Field(min_length=1)
    messages: list[AnthropicMessage] = Field(min_length=1)
    max_tokens: int = Field(gt=0)
    system: str | list[TextBlock] | None = None
    stream: bool = False
    tools: list[AnthropicTool | HostedSearch] = Field(default_factory=list)
    tool_choice: AnthropicChoice | None = None
    temperature: float | None = Field(default=None, ge=0, le=1)

    top_p: float | None = Field(default=None, ge=0, le=1)
    top_k: int | None = Field(default=None, gt=0)
    stop_sequences: list[str] = Field(default_factory=list)
    thinking: Annotated[ThinkingEnabled | ThinkingDisabled, Field(discriminator="type")] | None = (
        None
    )
    output_config: OutputConfig | None = None
    gateway: GatewayOptions = Field(default_factory=GatewayOptions)

    @model_validator(mode="after")
    def thinking_budget(self) -> Self:
        if (
            isinstance(self.thinking, ThinkingEnabled)
            and self.thinking.budget_tokens >= self.max_tokens
        ):
            raise ValueError("Thinking budget must be less than max_tokens")
        if self.top_k is not None and self.gateway.top_k is not None:
            raise ValueError("Specify top_k once")
        return self
