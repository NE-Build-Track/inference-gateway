"""Translate local conversation values into the runner's OpenAI request format."""

import json
from typing import Any

from endpoint_gateway.config import ModelRoute
from endpoint_gateway.core.models import Generate, NamedTool, Text, ToolCall, ToolResult


def runner_payload(request: Generate, route: ModelRoute) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    for message in request.messages:
        # Anthropic places tool results in user blocks; OpenAI requires individual tool messages.
        results = [p for p in message.content if isinstance(p, ToolResult)]
        for result in results:
            text = ("Tool error: " if result.is_error else "") + result.text
            messages.append({"role": "tool", "tool_call_id": result.call_id, "content": text})
        texts = [p.text for p in message.content if isinstance(p, Text)]
        calls = [p for p in message.content if isinstance(p, ToolCall)]
        if not results or texts or calls:
            wire: dict[str, Any] = {
                "role": message.role,
                "content": "".join(texts) if texts or not calls else None,
            }
            if calls:
                wire["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": call.arguments},
                    }
                    for call in calls
                ]
            messages.append(wire)
    payload: dict[str, Any] = {
        "messages": messages,
        "max_tokens": request.max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if route.upstream_model is not None:
        payload["model"] = route.upstream_model
    if route.disable_thinking:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {"name": t.name, "description": t.description, "parameters": t.schema},
            }
            for t in request.tools
        ]
        payload["tool_choice"] = (
            {"type": "function", "function": {"name": request.tool_choice.name}}
            if isinstance(request.tool_choice, NamedTool)
            else request.tool_choice
        )
        # Runners that honor this avoid interleaving; decoding still supports it.
        payload["parallel_tool_calls"] = False
    # Fail before dispatch if an injected caller supplied non-JSON or non-finite values.
    json.dumps(payload, allow_nan=False)
    return payload
