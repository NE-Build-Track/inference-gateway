"""Stateless OpenAI Responses subset over the shared generation contract."""

from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import replace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from inference_gateway.adapters.responses import ResponseIdentity, sse
from inference_gateway.adapters.schemas import OpenAIRequest
from inference_gateway.adapters.translate import openai_request
from inference_gateway.core.backend import UnsupportedResponse
from inference_gateway.core.models import (
    BlockEnd,
    BlockStart,
    Completion,
    Delta,
    Event,
    Finished,
    Generate,
    Text,
    ToolCall,
)


class ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str = Field(min_length=1)
    input: str | list[dict[str, Any]]
    instructions: str | None = None
    max_output_tokens: int = Field(default=1024, gt=0)
    stream: bool = False
    store: Literal[False] = False
    background: Literal[False] = False
    previous_response_id: None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    tool_choice: Literal["auto", "none", "required"] | dict[str, Any] = "auto"
    parallel_tool_calls: bool = False
    temperature: float | None = None
    top_p: float | None = None
    text: dict[str, Any] | None = None


def command(body: ResponsesRequest) -> Generate:
    messages: list[dict[str, Any]] = []
    if body.instructions is not None:
        messages.append({"role": "system", "content": body.instructions})
    items = [{"role": "user", "content": body.input}] if isinstance(body.input, str) else body.input
    for item in items:
        kind = item.get("type", "message")
        if kind == "function_call":
            if set(item) - {"type", "id", "call_id", "name", "arguments", "status"}:
                raise ValueError("Unsupported function call fields")
            call = {
                "id": item["call_id"],
                "type": "function",
                "function": {
                    "name": item["name"],
                    "arguments": item["arguments"],
                },
            }
            if messages and messages[-1].get("tool_calls"):
                messages[-1]["tool_calls"].append(call)
            else:
                messages.append({"role": "assistant", "content": None, "tool_calls": [call]})
        elif kind == "function_call_output":
            if set(item) - {"type", "id", "call_id", "output", "status"}:
                raise ValueError("Unsupported function output fields")
            messages.append(
                {"role": "tool", "tool_call_id": item["call_id"], "content": item["output"]}
            )
        elif kind == "message":
            if set(item) - {"type", "role", "content", "id", "status"}:
                raise ValueError("Unsupported message fields")
            content = item["content"]
            if isinstance(content, list):
                translated = []
                for part in content:
                    if not isinstance(part, dict):
                        raise ValueError("Invalid content")
                    if part.get("type") in {"input_text", "output_text"}:
                        if (
                            set(part) - {"type", "text", "annotations", "logprobs"}
                            or part.get("annotations")
                            or part.get("logprobs")
                        ):
                            raise ValueError("Unsupported text fields")
                        translated.append({"type": "text", "text": part["text"]})
                    elif part.get("type") == "input_image":
                        if set(part) - {"type", "image_url", "detail"}:
                            raise ValueError("Unsupported image fields")
                        translated.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": part["image_url"],
                                    "detail": part.get("detail", "auto"),
                                },
                            }
                        )
                    else:
                        raise ValueError("Unsupported input item")
                content = translated
            messages.append({"role": item["role"], "content": content})
        else:
            raise ValueError("Unsupported input item")
    tools = []
    for tool in body.tools:
        if tool.get("type") != "function" or set(tool) - {
            "type",
            "name",
            "description",
            "parameters",
            "strict",
        }:
            raise ValueError("Unsupported tool")
        if tool.get("strict"):
            raise ValueError("Strict function argument validation is unsupported")
        tools.append(
            {
                "type": "function",
                "function": {k: v for k, v in tool.items() if k not in {"type", "strict"}},
            }
        )
    choice = body.tool_choice
    if isinstance(choice, dict):
        if set(choice) != {"type", "name"} or choice["type"] != "function":
            raise ValueError("Unsupported tool choice")
        choice = {"type": "function", "function": {"name": choice["name"]}}
    fmt = None
    if body.text is not None:
        if set(body.text) != {"format"} or not isinstance(body.text["format"], dict):
            raise ValueError("Unsupported text options")
        value = body.text["format"]
        if value.get("type") == "json_schema":
            fmt = {
                "type": "json_schema",
                "json_schema": {k: v for k, v in value.items() if k != "type"},
            }
        elif value == {"type": "json_object"}:
            fmt = value
        elif value != {"type": "text"}:
            raise ValueError("Unsupported text format")
    return openai_request(
        OpenAIRequest.model_validate(
            {
                "model": body.model,
                "messages": messages,
                "tools": tools,
                "tool_choice": choice,
                "parallel_tool_calls": body.parallel_tool_calls,
                "max_tokens": body.max_output_tokens,
                "temperature": body.temperature,
                "top_p": body.top_p,
                "response_format": fmt,
            }
        )
    )


def output_item(part: Text | ToolCall, identity: ResponseIdentity, index: int) -> dict[str, Any]:
    if isinstance(part, Text):
        if part.citations:
            raise UnsupportedResponse("Responses citation mapping is unsupported")
        return {
            "id": f"msg_{identity.id}_{index}",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [
                {"type": "output_text", "text": part.text, "annotations": [], "logprobs": []}
            ],
        }
    if part.hosted:
        raise UnsupportedResponse("Hosted tools are unsupported")
    return {
        "id": f"fc_{identity.id}_{index}",
        "type": "function_call",
        "call_id": part.id,
        "name": part.name,
        "arguments": part.arguments,
        "status": "completed",
    }


def envelope(
    items: list[dict[str, Any]], identity: ResponseIdentity, finish: Finished | None
) -> dict[str, Any]:
    return {
        "id": identity.id,
        "object": "response",
        "created_at": identity.created,
        "model": identity.model,
        "status": ("incomplete" if finish.reason == "length" else "completed")
        if finish
        else "in_progress",
        "error": None,
        "incomplete_details": {"reason": "max_output_tokens"}
        if finish and finish.reason == "length"
        else None,
        "output": items,
        "store": False,
        "background": False,
        "previous_response_id": None,
        "usage": {
            "input_tokens": finish.input_tokens,
            "output_tokens": finish.output_tokens,
            "total_tokens": finish.input_tokens + finish.output_tokens,
            "input_tokens_details": {"cached_tokens": finish.cached_input_tokens},
        }
        if finish
        else None,
    }


def response(result: Completion, identity: ResponseIdentity) -> dict[str, Any]:
    items = []
    for index, part in enumerate(result.content):
        if not isinstance(part, Text | ToolCall):
            raise UnsupportedResponse("Unsupported Responses output item")
        items.append(output_item(part, identity, index))
    return envelope(items, identity, result.finish)


async def stream(
    events: AsyncGenerator[Event, None], identity: ResponseIdentity
) -> AsyncGenerator[str, None]:
    sequence = 0
    parts: dict[int, Text | ToolCall] = {}
    items: dict[int, dict[str, Any]] = {}

    def emit(kind: str, **values: Any) -> str:
        nonlocal sequence
        payload = {"type": kind, "sequence_number": sequence, **values}
        sequence += 1
        return sse(payload, kind)

    yield emit("response.created", response=envelope([], identity, None))
    yield emit("response.in_progress", response=envelope([], identity, None))
    async with aclosing(events):
        async for event in events:
            if isinstance(event, BlockStart):
                part = event.call or event.block or Text("")
                if not isinstance(part, Text | ToolCall):
                    raise UnsupportedResponse("Unsupported Responses output item")
                parts[event.index] = part
                item = output_item(part, identity, event.index)
                items[event.index] = item
                initial = {**item, "status": "in_progress"}
                if isinstance(part, Text):
                    initial["content"] = []
                yield emit("response.output_item.added", output_index=event.index, item=initial)
                if isinstance(part, Text):
                    yield emit(
                        "response.content_part.added",
                        item_id=item["id"],
                        output_index=event.index,
                        content_index=0,
                        part={"type": "output_text", "text": "", "annotations": [], "logprobs": []},
                    )
            elif isinstance(event, Delta):
                part = parts[event.index]
                item = items[event.index]
                if isinstance(part, Text):
                    parts[event.index] = replace(part, text=part.text + event.text)
                    yield emit(
                        "response.output_text.delta",
                        item_id=item["id"],
                        output_index=event.index,
                        content_index=0,
                        delta=event.text,
                        logprobs=[],
                    )
                else:
                    parts[event.index] = replace(part, arguments=part.arguments + event.text)
                    yield emit(
                        "response.function_call_arguments.delta",
                        item_id=item["id"],
                        output_index=event.index,
                        delta=event.text,
                    )
            elif isinstance(event, BlockEnd):
                part = parts[event.index]
                item = output_item(part, identity, event.index)
                items[event.index] = item
                if isinstance(part, Text):
                    yield emit(
                        "response.output_text.done",
                        item_id=item["id"],
                        output_index=event.index,
                        content_index=0,
                        text=part.text,
                        logprobs=[],
                    )
                    yield emit(
                        "response.content_part.done",
                        item_id=item["id"],
                        output_index=event.index,
                        content_index=0,
                        part=item["content"][0],
                    )
                else:
                    yield emit(
                        "response.function_call_arguments.done",
                        item_id=item["id"],
                        output_index=event.index,
                        name=part.name,
                        arguments=part.arguments,
                    )
                yield emit("response.output_item.done", output_index=event.index, item=item)
            elif isinstance(event, Finished):
                yield emit(
                    "response.incomplete" if event.reason == "length" else "response.completed",
                    response=envelope([items[i] for i in sorted(items)], identity, event),
                )
