"""Validated native feature fields and explicitly named gateway extensions."""

import base64
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from inference_gateway.core.models import JSON
from inference_gateway.core.structured import schema_example


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


def valid_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise ValueError("Expected an HTTP(S) URL without credentials")
    return value


def valid_data(value: str) -> str:
    if not value or len(value) > 8 * 1024 * 1024:
        raise ValueError("Invalid media size")
    base64.b64decode(value, validate=True)
    return value


class Source(WireModel):
    title: str
    text: str
    url: str

    @model_validator(mode="after")
    def valid(self) -> Self:
        valid_url(self.url)
        return self


class GatewayOptions(WireModel):
    sources: list[Source] = Field(default_factory=list, max_length=128)
    hosted_tools: list[Literal["web_search"]] = Field(default_factory=list, max_length=1)
    output_media: list[Literal["image", "audio"]] = Field(default_factory=list, max_length=2)
    reasoning: bool = False
    top_k: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.output_media)) != len(self.output_media):
            raise ValueError("Duplicate output modality")
        return self


class ImageURL(WireModel):
    url: str
    detail: Literal["auto", "low", "high"] = "auto"

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.url.startswith("data:"):
            header, data = self.url.split(",", 1)
            if header not in {f"data:image/{fmt};base64" for fmt in ("png", "jpeg", "webp", "gif")}:
                raise ValueError("Unsupported image data URL")
            valid_data(data)
        else:
            valid_url(self.url)
        return self


class ImageURLBlock(WireModel):
    type: Literal["image_url"]
    image_url: ImageURL


class AudioData(WireModel):
    data: str
    format: Literal["wav", "mp3"]

    @model_validator(mode="after")
    def valid(self) -> Self:
        valid_data(self.data)
        return self


class AudioInput(WireModel):
    type: Literal["input_audio"]
    input_audio: AudioData


class Base64Source(WireModel):
    type: Literal["base64"]
    media_type: Literal["image/jpeg", "image/png", "image/gif", "image/webp"]
    data: str

    @model_validator(mode="after")
    def valid(self) -> Self:
        valid_data(self.data)
        return self


class URLSource(WireModel):
    type: Literal["url"]
    url: str

    @model_validator(mode="after")
    def valid(self) -> Self:
        valid_url(self.url)
        return self


class ImageBlock(WireModel):
    type: Literal["image"]
    source: Annotated[Base64Source | URLSource, Field(discriminator="type")]


class ThinkingBlock(WireModel):
    type: Literal["thinking"]
    thinking: str
    signature: str


class TextSource(WireModel):
    type: Literal["text"]
    media_type: Literal["text/plain"]
    data: str


class CitationsConfig(WireModel):
    enabled: bool


class DocumentBlock(WireModel):
    type: Literal["document"]
    source: TextSource
    title: str = ""
    citations: CitationsConfig | None = None


class CharCitation(WireModel):
    type: Literal["char_location"]
    cited_text: str
    document_index: int = Field(ge=0)
    document_title: str | None = None
    start_char_index: int = Field(ge=0)
    end_char_index: int = Field(ge=0)
    file_id: None = None


class WebCitation(WireModel):
    type: Literal["web_search_result_location"]
    cited_text: str
    title: str
    url: str
    encrypted_index: str


class URLCitation(WireModel):
    title: str
    url: str
    start_index: int = Field(ge=0)
    end_index: int = Field(ge=0)


class Annotation(WireModel):
    type: Literal["url_citation"]
    url_citation: URLCitation


class SchemaDefinition(WireModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    description: str = ""
    schema_: dict[str, JSON] = Field(alias="schema")
    strict: Literal[True] = True

    @model_validator(mode="after")
    def valid(self) -> Self:
        schema_example(self.schema_)
        if self.schema_.get("type") != "object":
            raise ValueError("Output schema root must be an object")
        return self


class ResponseFormat(WireModel):
    type: Literal["text", "json_object", "json_schema"]
    json_schema: SchemaDefinition | None = None

    @model_validator(mode="after")
    def valid(self) -> Self:
        if (self.type == "json_schema") != (self.json_schema is not None):
            raise ValueError("json_schema is required only with json_schema type")
        return self


class JSONFormat(WireModel):
    type: Literal["json_schema"]
    schema_: dict[str, JSON] = Field(alias="schema")

    @model_validator(mode="after")
    def valid(self) -> Self:
        schema_example(self.schema_)
        if self.schema_.get("type") != "object":
            raise ValueError("Output schema root must be an object")
        return self


class OutputConfig(WireModel):
    format: JSONFormat


class ThinkingEnabled(WireModel):
    type: Literal["enabled"]
    budget_tokens: int = Field(ge=1024)


class ThinkingDisabled(WireModel):
    type: Literal["disabled"]


class AudioOutput(WireModel):
    format: Literal["wav", "pcm16"]
    voice: str = Field(min_length=1)


class HostedSearch(WireModel):
    type: Literal["web_search_20250305"]
    name: Literal["web_search"]
    max_uses: Literal[1] = 1


class ServerToolUse(WireModel):
    type: Literal["server_tool_use"]
    id: str = Field(min_length=1)
    name: Literal["web_search"]
    input: dict[str, JSON]
    caller: None = None


class SearchResult(WireModel):
    type: Literal["web_search_result"]
    title: str
    url: str
    encrypted_content: str
    page_age: None = None


class SearchResults(WireModel):
    type: Literal["web_search_tool_result"]
    tool_use_id: str
    content: list[SearchResult]
    caller: None = None


class MediaOutputBlock(WireModel):
    type: Literal["gateway_media"]
    kind: Literal["image", "audio"]
    data: str
    media_type: Literal["image/png", "audio/wav", "audio/pcm16"]

    @model_validator(mode="after")
    def valid(self) -> Self:
        valid_data(self.data)
        if not self.media_type.startswith(self.kind + "/"):
            raise ValueError("Media type does not match kind")
        return self


class GatewayHistoryBlock(WireModel):
    index: int = Field(ge=0)
    block: Annotated[ServerToolUse | SearchResults | MediaOutputBlock, Field(discriminator="type")]
