from dataclasses import dataclass
from typing import Literal

type JSON = None | bool | int | float | str | list[JSON] | dict[str, JSON]


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    text: str
    is_error: bool = False


type Content = Text | ToolCall | ToolResult


@dataclass(frozen=True)
class Message:
    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: tuple[Content, ...]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict[str, JSON]


@dataclass(frozen=True)
class NamedTool:
    name: str


@dataclass(frozen=True)
class Generate:
    model: str
    messages: tuple[Message, ...]
    tools: tuple[Tool, ...] = ()
    tool_choice: Literal["auto", "none", "required"] | NamedTool = "auto"
    max_tokens: int = 1024
    temperature: float | None = None
    request_id: str = ""  # Server-generated correlation/operation reference, never a credential.


@dataclass(frozen=True)
class BlockStart:
    index: int
    call: ToolCall | None = None


@dataclass(frozen=True)
class Delta:
    index: int
    text: str  # Text or a fragment of a tool's JSON arguments.


@dataclass(frozen=True)
class BlockEnd:
    index: int


@dataclass(frozen=True)
class Finished:
    reason: Literal["stop", "length", "tool_calls"]
    input_tokens: int = 0
    output_tokens: int = 0


type Event = BlockStart | Delta | BlockEnd | Finished


@dataclass(frozen=True)
class Completion:
    content: tuple[Text | ToolCall, ...]
    finish: Finished
