"""Translate local conversation values into the runner's OpenAI request format."""

import json
from typing import Any

from inference_gateway.config import ModelRoute
from inference_gateway.core.backend import UnsupportedFeature
from inference_gateway.core.capabilities import required_features
from inference_gateway.core.models import (
    Generate,
    Media,
    NamedTool,
    Reasoning,
    Text,
    ToolCall,
    ToolResult,
)


def runner_payload(request: Generate, route: ModelRoute) -> dict[str, Any]:
    if required_features(request) - route.capabilities:
        raise UnsupportedFeature("Feature is not enabled for this model route")
    if request.reasoning_effort not in (None, "none") and (
        request.reasoning_effort not in route.reasoning_efforts
    ):
        raise UnsupportedFeature("Reasoning effort is not enabled for this model route")
    if request.reasoning_budget is not None and (
        not request.reasoning or request.reasoning_effort == "none"
    ):
        raise UnsupportedFeature("Reasoning budget requires enabled reasoning")
    if any(isinstance(p, Reasoning) and p.signature for m in request.messages for p in m.content):
        raise UnsupportedFeature("Runner cannot preserve signed reasoning history")
    if request.sampling.stop and (
        request.tools
        or request.reasoning
        or request.reasoning_effort not in (None, "none")
        or not route.disable_thinking
    ):
        raise UnsupportedFeature("Stop controls require a text-only, non-thinking request")
    messages: list[dict[str, Any]] = []
    for message in request.messages:
        # Anthropic places tool results in user blocks; OpenAI requires individual tool messages.
        results = [p for p in message.content if isinstance(p, ToolResult)]
        for result in results:
            text = ("Tool error: " if result.is_error else "") + result.text
            messages.append({"role": "tool", "tool_call_id": result.call_id, "content": text})
        texts = [p.text for p in message.content if isinstance(p, Text)]
        calls = [p for p in message.content if isinstance(p, ToolCall)]
        media = [p for p in message.content if isinstance(p, Media)]
        if not results or texts or calls or media:
            wire: dict[str, Any] = {
                "role": message.role,
                "content": "".join(texts) if texts or not calls else None,
            }
            if media:
                if message.role != "user":
                    raise UnsupportedFeature("Runner media inputs require user messages")
                parts: list[dict[str, Any]] = []
                for part in message.content:
                    if isinstance(part, Text):
                        parts.append({"type": "text", "text": part.text})
                    elif isinstance(part, Media):
                        if part.kind not in route.input_media:
                            raise UnsupportedFeature("Media kind is not enabled for this route")
                        if part.kind == "image":
                            url = (
                                part.data
                                if part.is_url
                                else (f"data:{part.media_type};base64,{part.data}")
                            )
                            parts.append(
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": url,
                                        "detail": part.detail,
                                    },
                                }
                            )
                        else:
                            formats = {"audio/wav": "wav", "audio/mpeg": "mp3"}
                            if part.is_url or part.media_type not in formats:
                                raise UnsupportedFeature("Runner requires inline WAV or MP3 input")
                            parts.append(
                                {
                                    "type": "input_audio",
                                    "input_audio": {
                                        "data": part.data,
                                        "format": formats[part.media_type],
                                    },
                                }
                            )
                wire["content"] = parts
            reasoning = [p.text for p in message.content if isinstance(p, Reasoning)]
            if reasoning:
                wire["reasoning_content"] = "".join(reasoning)
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
    payload["model"] = route.name
    for name in ("top_p", "top_k", "frequency_penalty", "presence_penalty", "seed"):
        value = getattr(request.sampling, name)
        if value is not None:
            payload[name] = value
    if request.reasoning_effort is not None:
        payload["reasoning_effort"] = request.reasoning_effort
    if request.reasoning_budget is not None:
        payload["thinking_token_budget"] = request.reasoning_budget
    if request.reasoning or request.reasoning_effort is not None or route.disable_thinking:
        payload["chat_template_kwargs"] = {
            "enable_thinking": request.reasoning or request.reasoning_effort not in (None, "none")
        }
    if request.output_format is not None:
        fmt = request.output_format
        payload["response_format"] = (
            {"type": "json_object"}
            if fmt.schema is None
            else {
                "type": "json_schema",
                "json_schema": {"name": fmt.name, "strict": True, "schema": fmt.schema},
            }
        )
    if request.sampling.stop:
        payload["stop"] = list(request.sampling.stop)
        # Request the marker so the gateway can identify it for Anthropic.
        # The decoder withholds/strips it even across arbitrary chunk boundaries.
        payload["include_stop_str_in_output"] = True
    if request.temperature is not None:
        payload["temperature"] = request.temperature
    if request.tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.schema,
                    "strict": t.strict,
                },
            }
            for t in request.tools
        ]
        payload["tool_choice"] = (
            {"type": "function", "function": {"name": request.tool_choice.name}}
            if isinstance(request.tool_choice, NamedTool)
            else request.tool_choice
        )
        # Runners that honor this avoid interleaving; decoding still supports it.
        payload["parallel_tool_calls"] = request.parallel_tools
    # Fail before dispatch if an injected caller supplied non-JSON or non-finite values.
    json.dumps(payload, allow_nan=False)
    return payload
