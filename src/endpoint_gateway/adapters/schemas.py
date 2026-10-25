"""Supported wire contracts. Unknown fields fail instead of disappearing."""

import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from endpoint_gateway.core.models import JSON


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TextBlock(WireModel):
    type: Literal["text"] = "text"
    text: str


def object_schema() -> dict[str, JSON]:
    return {"type": "object"}


class Function(WireModel):
    name: str = Field(min_length=1)
    description: str = ""
    parameters: dict[str, JSON] = Field(default_factory=object_schema)


class OpenAITool(WireModel):
    type: Literal["function"] = "function"
    function: Function


class CallFunction(WireModel):
    name: str = Field(min_length=1)
    arguments: str

    @model_validator(mode="after")
    def valid_json(self) -> Self:
        if not isinstance(json.loads(self.arguments), dict):
            raise ValueError("Tool arguments must encode a JSON object")
        return self


class OpenAICall(WireModel):
    id: str = Field(min_length=1)
    type: Literal["function"] = "function"
    function: CallFunction


class OpenAIMessage(WireModel):
    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: str | list[TextBlock] | None = None
    tool_calls: list[OpenAICall] = Field(default_factory=list)
    tool_call_id: str | None = None

    @model_validator(mode="after")
    def valid_role(self) -> Self:
        if self.tool_calls and self.role != "assistant":
            raise ValueError("Only assistant messages may contain tool_calls")
        if (self.role == "tool") != (self.tool_call_id is not None):
            raise ValueError("tool_call_id is required only for tool messages")
        if self.content is None and not self.tool_calls:
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

    @model_validator(mode="after")
    def token_limit(self) -> Self:
        if self.max_tokens is not None and self.max_completion_tokens is not None:
            raise ValueError("Specify one token limit")
        if self.stream_options is not None and not self.stream:
            raise ValueError("stream_options requires stream=true")
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


type AnthropicBlock = Annotated[TextBlock | ToolUse | ResultBlock, Field(discriminator="type")]


class AnthropicMessage(WireModel):
    role: Literal["user", "assistant"]
    content: str | list[AnthropicBlock]

    @model_validator(mode="after")
    def valid_role(self) -> Self:
        if isinstance(self.content, list):
            for block in self.content:
                if isinstance(block, ToolUse) and self.role != "assistant":
                    raise ValueError("tool_use requires assistant role")
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
    tools: list[AnthropicTool] = Field(default_factory=list)
    tool_choice: AnthropicChoice | None = None
    temperature: float | None = Field(default=None, ge=0, le=1)
