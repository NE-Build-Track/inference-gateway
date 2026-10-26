from dataclasses import dataclass
from typing import Literal

type JSON = None | bool | int | float | str | list[JSON] | dict[str, JSON]


@dataclass(frozen=True)
class Citation:
    title: str
    text: str
    url: str | None = None
    document_index: int | None = None
    start: int = 0
    end: int = 0
    token: str = ""


@dataclass(frozen=True)
class Text:
    text: str
    citations: tuple[Citation, ...] = ()


@dataclass(frozen=True)
class Reasoning:
    text: str
    signature: str = ""


@dataclass(frozen=True)
class Media:
    kind: Literal["image", "audio"]
    data: str
    media_type: str
    is_url: bool = False
    detail: str = "auto"


@dataclass(frozen=True)
class Document:
    text: str
    title: str = ""
    cite: bool = False


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str
    hosted: bool = False


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    text: str
    is_error: bool = False


@dataclass(frozen=True)
class HostedResult:
    call_id: str
    results: tuple[Citation, ...]


type Output = Text | Reasoning | ToolCall | HostedResult | Media
type Content = Output | ToolResult | Document


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
class Sampling:
    top_p: float | None = None
    top_k: int | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    seed: int | None = None
    stop: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutputFormat:
    schema: dict[str, JSON] | None = None  # None means JSON object mode.
    name: str = "response"


@dataclass(frozen=True)
class Generate:
    model: str
    messages: tuple[Message, ...]
    tools: tuple[Tool, ...] = ()
    tool_choice: Literal["auto", "none", "required"] | NamedTool = "auto"
    max_tokens: int = 1024
    temperature: float | None = None
    request_id: str = ""  # Correlation, never a credential.
    sampling: Sampling = Sampling()
    reasoning: bool = False
    reasoning_effort: str | None = None
    reasoning_budget: int | None = None
    output_format: OutputFormat | None = None
    parallel_tools: bool = False
    hosted_search: bool = False
    sources: tuple[Citation, ...] = ()
    output_media: tuple[Literal["image", "audio"], ...] = ()
    audio_format: Literal["wav", "pcm16"] = "wav"
    audio_voice: str = "alloy"
    output_text: bool = True


@dataclass(frozen=True)
class BlockStart:
    index: int
    call: ToolCall | None = None
    block: Text | Reasoning | HostedResult | Media | None = None


@dataclass(frozen=True)
class Delta:
    index: int
    text: str


@dataclass(frozen=True)
class BlockEnd:
    index: int


@dataclass(frozen=True)
class Finished:
    reason: Literal["stop", "length", "tool_calls"]
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    stop_sequence: str | None = None


type Event = BlockStart | Delta | BlockEnd | Finished


@dataclass(frozen=True)
class Completion:
    content: tuple[Output, ...]
    finish: Finished
