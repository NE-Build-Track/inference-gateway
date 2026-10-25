import json
from typing import Literal

from endpoint_gateway.adapters.schemas import (
    AnthropicRequest,
    OpenAIChoice,
    OpenAIRequest,
    ResultBlock,
    TextBlock,
    ToolUse,
)
from endpoint_gateway.core.models import (
    Content,
    Generate,
    Message,
    NamedTool,
    Text,
    Tool,
    ToolCall,
    ToolResult,
)


def openai_request(body: OpenAIRequest) -> Generate:
    messages: list[Message] = []
    for message in body.messages:
        text = message.content
        parts: list[Content] = []
        if text is not None:
            value = text if isinstance(text, str) else "".join(p.text for p in text)
            parts.append(
                ToolResult(message.tool_call_id, value)
                if message.tool_call_id is not None
                else Text(value)
            )
        parts.extend(
            ToolCall(c.id, c.function.name, c.function.arguments) for c in message.tool_calls
        )
        messages.append(Message(message.role, tuple(parts)))
    tools = tuple(
        Tool(t.function.name, t.function.description, t.function.parameters) for t in body.tools
    )
    choice = (
        NamedTool(body.tool_choice.function.name)
        if isinstance(body.tool_choice, OpenAIChoice)
        else body.tool_choice
    )
    return validate(
        Generate(
            body.model,
            tuple(messages),
            tools,
            choice,
            body.max_completion_tokens or body.max_tokens or 1024,
            body.temperature,
        )
    )


def anthropic_request(body: AnthropicRequest) -> Generate:
    messages: list[Message] = []
    if body.system is not None:
        text = body.system if isinstance(body.system, str) else "".join(p.text for p in body.system)
        messages.append(Message("system", (Text(text),)))
    for message in body.messages:
        parts: list[Content] = []
        if isinstance(message.content, str):
            parts.append(Text(message.content))
        else:
            for block in message.content:
                match block:
                    case TextBlock():
                        parts.append(Text(block.text))
                    case ToolUse():
                        parts.append(ToolCall(block.id, block.name, json.dumps(block.input)))
                    case ResultBlock():
                        text = (
                            block.content
                            if isinstance(block.content, str)
                            else "".join(p.text for p in block.content)
                        )
                        parts.append(ToolResult(block.tool_use_id, text, block.is_error))
        messages.append(Message(message.role, tuple(parts)))
    tools = tuple(Tool(t.name, t.description, t.input_schema) for t in body.tools)
    choice: Literal["auto", "none", "required"] | NamedTool = "auto"
    if body.tool_choice is not None:
        if body.tool_choice.name is not None:
            choice = NamedTool(body.tool_choice.name)
        elif body.tool_choice.type == "any":
            choice = "required"
        elif body.tool_choice.type in {"auto", "none"}:
            choice = "none" if body.tool_choice.type == "none" else "auto"
    return validate(
        Generate(body.model, tuple(messages), tools, choice, body.max_tokens, body.temperature)
    )


def validate(request: Generate) -> Generate:
    names = [tool.name for tool in request.tools]
    if len(names) != len(set(names)):
        raise ValueError("Tool names must be unique")
    if request.tool_choice == "required" and not names:
        raise ValueError("Required tool choice needs tools")
    if isinstance(request.tool_choice, NamedTool) and request.tool_choice.name not in names:
        raise ValueError("Selected tool is not defined")
    pending: set[str] = set()
    seen: set[str] = set()
    for message in request.messages:
        for part in message.content:
            if isinstance(part, ToolCall):
                if part.id in seen:
                    raise ValueError("Tool call IDs must be unique")
                seen.add(part.id)
                pending.add(part.id)
            elif isinstance(part, ToolResult):
                if part.call_id not in pending:
                    raise ValueError("Tool result must reference an outstanding tool call")
                pending.remove(part.call_id)
    if pending:
        raise ValueError("Supply results for outstanding tool calls before generating")
    return request
