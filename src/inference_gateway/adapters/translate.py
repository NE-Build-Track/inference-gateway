import json
from typing import Literal

from inference_gateway.adapters.feature_schemas import (
    AudioInput,
    Base64Source,
    CharCitation,
    DocumentBlock,
    HostedSearch,
    ImageBlock,
    ImageURLBlock,
    MediaOutputBlock,
    SearchResults,
    ServerToolUse,
    ThinkingBlock,
    ThinkingEnabled,
    WebCitation,
)
from inference_gateway.adapters.schemas import (
    AnthropicRequest,
    AnthropicTool,
    OpenAIChoice,
    OpenAIRequest,
    ResultBlock,
    TextBlock,
    ToolUse,
)
from inference_gateway.core.models import (
    Citation,
    Content,
    Document,
    Generate,
    HostedResult,
    Media,
    Message,
    NamedTool,
    OutputFormat,
    Reasoning,
    Sampling,
    Text,
    Tool,
    ToolCall,
    ToolResult,
)
from inference_gateway.core.structured import validate_strict_schema


def text_block(block: TextBlock) -> Text:
    citations = []
    for c in block.citations or []:
        if isinstance(c, CharCitation):
            if c.end_char_index < c.start_char_index:
                raise ValueError("Invalid citation range")
            citations.append(
                Citation(
                    c.document_title or "",
                    c.cited_text,
                    document_index=c.document_index,
                    start=c.start_char_index,
                    end=c.end_char_index,
                )
            )
        elif isinstance(c, WebCitation):
            citations.append(Citation(c.title, c.cited_text, c.url, token=c.encrypted_index))
    return Text(block.text, tuple(citations))


def audio_input(block: AudioInput) -> Media:
    return Media(
        "audio",
        block.input_audio.data,
        "audio/wav" if block.input_audio.format == "wav" else "audio/mpeg",
    )


def openai_request(body: OpenAIRequest) -> Generate:
    messages: list[Message] = []
    for message in body.messages:
        parts: list[Content] = []
        if message.reasoning_content is not None:
            parts.append(Reasoning(message.reasoning_content))
        if isinstance(message.content, str):
            citations = tuple(
                Citation(
                    a.url_citation.title,
                    message.content[a.url_citation.start_index : a.url_citation.end_index],
                    a.url_citation.url,
                    start=a.url_citation.start_index,
                    end=a.url_citation.end_index,
                )
                for a in message.annotations or []
            )
            if any(c.end < c.start or c.end > len(message.content) for c in citations):
                raise ValueError("Invalid citation range")
            parts.append(Text(message.content, citations))
        elif isinstance(message.content, list):
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append(text_block(block))
                elif isinstance(block, AudioInput):
                    parts.append(audio_input(block))
                else:
                    assert isinstance(block, ImageURLBlock)
                    url = block.image_url.url
                    if url.startswith("data:"):
                        header, data = url.split(",", 1)
                        parts.append(
                            Media(
                                "image",
                                data,
                                header[5:].split(";")[0],
                                detail=block.image_url.detail,
                            )
                        )
                    else:
                        parts.append(Media("image", url, "", True, block.image_url.detail))
        for entry in message.gateway_blocks:
            history_block = entry.block
            if isinstance(history_block, ServerToolUse):
                parts.append(
                    ToolCall(
                        history_block.id, history_block.name, json.dumps(history_block.input), True
                    )
                )
            elif isinstance(history_block, SearchResults):
                parts.append(
                    HostedResult(
                        history_block.tool_use_id,
                        tuple(
                            Citation(r.title, "", r.url, token=r.encrypted_content)
                            for r in history_block.content
                        ),
                    )
                )
            else:
                parts.append(
                    Media(history_block.kind, history_block.data, history_block.media_type)
                )
        if message.gateway_citations:
            if not isinstance(message.content, str):
                raise ValueError("Gateway citations require text content")
            parts = [
                text_block(TextBlock(text=p.text, citations=list(message.gateway_citations)))
                if isinstance(p, Text)
                else p
                for p in parts
            ]
        if message.tool_call_id is not None:
            parts = [
                ToolResult(
                    message.tool_call_id, "".join(p.text for p in parts if isinstance(p, Text))
                )
            ]
        parts.extend(
            ToolCall(c.id, c.function.name, c.function.arguments) for c in message.tool_calls
        )
        messages.append(Message(message.role, tuple(parts)))
    tools = tuple(
        Tool(t.function.name, t.function.description, t.function.parameters, t.function.strict)
        for t in body.tools
    )
    choice = (
        NamedTool(body.tool_choice.function.name)
        if isinstance(body.tool_choice, OpenAIChoice)
        else body.tool_choice
    )
    fmt = body.response_format
    output = (
        None
        if fmt is None or fmt.type == "text"
        else OutputFormat(
            fmt.json_schema.schema_ if fmt.json_schema else None,
            fmt.json_schema.name if fmt.json_schema else "response",
        )
    )
    stop = (body.stop,) if isinstance(body.stop, str) else tuple(body.stop or [])
    media: list[Literal["image", "audio"]] = list(body.gateway.output_media)
    if body.audio and "audio" not in media:
        media.append("audio")
    return validate(
        Generate(
            body.model,
            tuple(messages),
            tools,
            choice,
            body.max_completion_tokens or body.max_tokens or 1024,
            body.temperature,
            sampling=Sampling(
                body.top_p,
                body.gateway.top_k,
                body.frequency_penalty,
                body.presence_penalty,
                body.seed,
                stop,
            ),
            reasoning=body.gateway.reasoning or body.reasoning_effort not in (None, "none"),
            reasoning_effort=body.reasoning_effort,
            output_format=output,
            parallel_tools=body.parallel_tool_calls,
            hosted_search=bool(body.gateway.hosted_tools),
            sources=tuple(
                Citation(s.title, s.text, s.url, token="gateway-source")
                for s in body.gateway.sources
            ),
            output_media=tuple(m for m in media if m in ("image", "audio")),
            audio_format=body.audio.format if body.audio else "wav",
            audio_voice=body.audio.voice if body.audio else "alloy",
            output_text=body.modalities is None or "text" in body.modalities,
        )
    )


def anthropic_request(body: AnthropicRequest) -> Generate:
    messages: list[Message] = []
    if body.system is not None:
        text = body.system if isinstance(body.system, str) else "".join(p.text for p in body.system)
        if isinstance(body.system, list) and any(p.citations for p in body.system):
            raise ValueError("System citations are unsupported")
        messages.append(Message("system", (Text(text),)))
    for message in body.messages:
        parts: list[Content] = []
        if isinstance(message.content, str):
            parts.append(Text(message.content))
        else:
            for block in message.content:
                match block:
                    case TextBlock():
                        parts.append(text_block(block))
                    case ThinkingBlock():
                        parts.append(Reasoning(block.thinking, block.signature))
                    case ToolUse():
                        parts.append(ToolCall(block.id, block.name, json.dumps(block.input)))
                    case ServerToolUse():
                        parts.append(ToolCall(block.id, block.name, json.dumps(block.input), True))
                    case SearchResults():
                        parts.append(
                            HostedResult(
                                block.tool_use_id,
                                tuple(
                                    Citation(r.title, "", r.url, token=r.encrypted_content)
                                    for r in block.content
                                ),
                            )
                        )
                    case ResultBlock():
                        if isinstance(block.content, list) and any(
                            p.citations for p in block.content
                        ):
                            raise ValueError("Tool-result citations unsupported")
                        text = (
                            block.content
                            if isinstance(block.content, str)
                            else "".join(p.text for p in block.content)
                        )
                        parts.append(ToolResult(block.tool_use_id, text, block.is_error))
                    case MediaOutputBlock():
                        parts.append(Media(block.kind, block.data, block.media_type))
                    case AudioInput():
                        parts.append(audio_input(block))
                    case ImageBlock():
                        src = block.source
                        parts.append(
                            Media("image", src.data, src.media_type)
                            if isinstance(src, Base64Source)
                            else Media("image", src.url, "", True)
                        )
                    case DocumentBlock():
                        parts.append(
                            Document(
                                block.source.data,
                                block.title,
                                bool(block.citations and block.citations.enabled),
                            )
                        )
        messages.append(Message(message.role, tuple(parts)))
    tools = tuple(
        Tool(t.name, t.description, t.input_schema)
        for t in body.tools
        if isinstance(t, AnthropicTool)
    )
    hosted = [t for t in body.tools if isinstance(t, HostedSearch)]
    if len(hosted) > 1 or (hosted and body.gateway.hosted_tools):
        raise ValueError("Duplicate hosted tool")
    choice: Literal["auto", "none", "required"] | NamedTool = "auto"
    if body.tool_choice is not None:
        if body.tool_choice.name is not None:
            choice = NamedTool(body.tool_choice.name)
        elif body.tool_choice.type == "any":
            choice = "required"
        elif body.tool_choice.type == "none":
            choice = "none"
    return validate(
        Generate(
            body.model,
            tuple(messages),
            tools,
            choice,
            body.max_tokens,
            body.temperature,
            sampling=Sampling(
                top_p=body.top_p,
                top_k=body.top_k or body.gateway.top_k,
                stop=tuple(body.stop_sequences),
            ),
            reasoning=isinstance(body.thinking, ThinkingEnabled) or body.gateway.reasoning,
            reasoning_budget=body.thinking.budget_tokens
            if isinstance(body.thinking, ThinkingEnabled)
            else None,
            output_format=OutputFormat(body.output_config.format.schema_)
            if body.output_config
            else None,
            parallel_tools=bool(
                body.tool_choice
                and "disable_parallel_tool_use" in body.tool_choice.model_fields_set
                and not body.tool_choice.disable_parallel_tool_use
            ),
            hosted_search=bool(hosted or body.gateway.hosted_tools),
            sources=tuple(
                Citation(s.title, s.text, s.url, token="gateway-source")
                for s in body.gateway.sources
            ),
            output_media=tuple(body.gateway.output_media),
        )
    )


def validate(request: Generate) -> Generate:
    for tool in request.tools:
        if tool.strict:
            validate_strict_schema(tool.schema)
    names = [tool.name for tool in request.tools] + (
        ["web_search"] if request.hosted_search else []
    )
    if len(names) != len(set(names)):
        raise ValueError("Tool names must be unique")
    if request.tool_choice == "required" and not names:
        raise ValueError("Required tool choice needs tools")
    if isinstance(request.tool_choice, NamedTool) and request.tool_choice.name not in names:
        raise ValueError("Selected tool is not defined")
    if len(request.sampling.stop) > 4 or any(not s for s in request.sampling.stop):
        raise ValueError("One to four nonempty stop sequences supported")
    if request.output_format and (
        request.hosted_search
        or request.output_media
        or request.sources
        or any(isinstance(p, Document) and p.cite for m in request.messages for p in m.content)
    ):
        raise ValueError(
            "Structured output cannot combine with hosted tools, citations or media output"
        )
    if not request.output_text and (
        request.sources or request.hosted_search or request.output_format
    ):
        raise ValueError("Requested feature requires text output")
    if request.output_format and request.sampling.stop:
        raise ValueError("Structured output cannot combine with stop sequences")
    if request.tools and (request.output_media or request.sources or request.hosted_search):
        raise ValueError("Client tools cannot combine with media output, sources or hosted tools")
    if request.tools and any(
        isinstance(p, Document) and p.cite for m in request.messages for p in m.content
    ):
        raise ValueError("Client tools cannot combine with cited documents")
    pending: dict[str, bool] = {}
    seen: set[str] = set()
    for message in request.messages:
        for part in message.content:
            if isinstance(part, ToolCall):
                if part.id in seen:
                    raise ValueError("Tool call IDs must be unique")
                seen.add(part.id)
                pending[part.id] = part.hosted
            elif isinstance(part, (ToolResult, HostedResult)):
                if part.call_id not in pending or pending[part.call_id] != isinstance(
                    part, HostedResult
                ):
                    raise ValueError("Result must reference an outstanding call of the same kind")
                del pending[part.call_id]
    if pending:
        raise ValueError("Supply results for outstanding tool calls before generating")
    return request
