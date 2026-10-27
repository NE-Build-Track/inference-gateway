"""Native stateless Responses runner transport; bounded text and function-call events."""

import json
from typing import Any, Literal

from inference_gateway.backends.livepeer_request import runner_payload
from inference_gateway.backends.openai_events import Usage
from inference_gateway.config import ModelRoute
from inference_gateway.core.backend import UnsupportedFeature, UnsupportedResponse
from inference_gateway.core.models import (
    BlockEnd,
    BlockStart,
    Delta,
    Event,
    Finished,
    Generate,
    ToolCall,
)


def payload(request: Generate, route: ModelRoute) -> dict[str, Any]:
    chat = runner_payload(request, route)
    unsupported = {
        "stop",
        "seed",
        "top_k",
        "frequency_penalty",
        "presence_penalty",
        "thinking_token_budget",
        "reasoning_effort",
    } & chat.keys()
    if unsupported or request.reasoning or not route.disable_thinking:
        raise UnsupportedFeature(
            "Native Responses supports text, images, functions and structured text"
        )
    inputs = []
    for message in chat["messages"]:
        if message.get("reasoning_content"):
            raise UnsupportedFeature("Native Responses reasoning history is unsupported")
        if message["role"] == "tool":
            inputs.append(
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": message["content"],
                }
            )
            continue
        content = message["content"]
        if isinstance(content, list):
            parts = []
            for part in content:
                if part["type"] == "text":
                    parts.append({"type": "input_text", "text": part["text"]})
                elif part["type"] == "image_url":
                    parts.append(
                        {
                            "type": "input_image",
                            "image_url": part["image_url"]["url"],
                            "detail": part["image_url"]["detail"],
                        }
                    )
                else:
                    raise UnsupportedFeature("Native Responses audio is unsupported")
            content = parts
        if content:
            inputs.append({"role": message["role"], "content": content})
        for call in message.get("tool_calls", []):
            inputs.append({"type": "function_call", "call_id": call["id"], **call["function"]})
    out: dict[str, Any] = {
        "model": route.name,
        "input": inputs,
        "stream": True,
        "store": False,
        "max_output_tokens": request.max_tokens,
    }
    for field in ("temperature", "top_p", "parallel_tool_calls"):
        if field in chat:
            out[field] = chat[field]
    if request.tools:
        out["tools"] = [{"type": "function", **t["function"]} for t in chat["tools"]]
        choice = chat["tool_choice"]
        out["tool_choice"] = (
            {"type": "function", "name": choice["function"]["name"]}
            if isinstance(choice, dict)
            else choice
        )
    if "response_format" in chat:
        fmt = chat["response_format"]
        out["text"] = {
            "format": {"type": "json_schema", **fmt["json_schema"]}
            if fmt["type"] == "json_schema"
            else fmt
        }
    return out


class ResponsesDecoder:
    def __init__(self, limit: int) -> None:
        self.done = False
        self._limit = limit
        self._bytes = 0
        self._id: str | None = None
        self._sequence = -1
        self._items: dict[int, dict[str, Any]] = {}
        self._text: dict[int, str] = {}
        self._closed: set[int] = set()
        self._content: set[int] = set()
        self._text_done: set[int] = set()
        self._content_done: set[int] = set()

    def feed(self, data: str) -> list[Event]:
        try:
            return self._feed(json.loads(data))
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
            raise UnsupportedResponse("Malformed native Responses stream") from exc

    def _feed(self, value: Any) -> list[Event]:
        if self.done or not isinstance(value, dict):
            raise UnsupportedResponse("Invalid response event")
        seq = value["sequence_number"]
        if type(seq) is not int or seq <= self._sequence:
            raise UnsupportedResponse("Invalid response event sequence")
        self._sequence = seq
        kind = value["type"]
        if kind in {"response.created", "response.in_progress"}:
            response = value["response"]
            identity = response["id"]
            if not isinstance(identity, str) or not identity or (self._id and self._id != identity):
                raise UnsupportedResponse("Response identity changed")
            if kind == "response.created" and self._id:
                raise UnsupportedResponse("Duplicate response creation")
            self._id = identity
            return []
        if self._id is None:
            raise UnsupportedResponse("Missing response creation")
        if kind in {"response.completed", "response.incomplete"}:
            result = value["response"]
            if result["id"] != self._id or result.get("error"):
                raise UnsupportedResponse("Invalid final response")
            if result["status"] != kind.removeprefix("response."):
                raise UnsupportedResponse("Response status mismatch")
            if kind == "response.completed" and any(
                i.get("status") != "completed" for i in self._items.values()
            ):
                raise UnsupportedResponse("Completed response has unfinished items")
            if set(self._items) != self._closed or result["output"] != [
                self._items[i] for i in sorted(self._items)
            ]:
                raise UnsupportedResponse("Final output differs from streamed output")
            raw = result["usage"]
            usage = Usage.model_validate(
                {
                    "prompt_tokens": raw["input_tokens"],
                    "completion_tokens": raw["output_tokens"],
                    "prompt_tokens_details": raw.get("input_tokens_details"),
                }
            )
            if (
                usage.prompt_tokens_details
                and usage.prompt_tokens_details.cached_tokens > usage.prompt_tokens
            ):
                raise UnsupportedResponse("Invalid cached usage")
            if (
                type(raw["total_tokens"]) is not int
                or raw["total_tokens"] != usage.prompt_tokens + usage.completion_tokens
            ):
                raise UnsupportedResponse("Invalid total usage")
            if (
                kind == "response.incomplete"
                and result.get("incomplete_details", {}).get("reason") != "max_output_tokens"
            ):
                raise UnsupportedResponse("Unsupported incomplete response")
            reason: Literal["stop", "length", "tool_calls"] = (
                "length"
                if kind == "response.incomplete"
                else "tool_calls"
                if any(i["type"] == "function_call" for i in self._items.values())
                else "stop"
            )
            if not self._items:
                raise UnsupportedResponse("Empty native output")
            self.done = True
            return [
                Finished(
                    reason,
                    usage.prompt_tokens,
                    usage.completion_tokens,
                    usage.prompt_tokens_details.cached_tokens if usage.prompt_tokens_details else 0,
                )
            ]
        index = value["output_index"]
        if type(index) is not int or index < 0:
            raise UnsupportedResponse("Invalid output index")
        if kind == "response.output_item.added":
            item = value["item"]
            if (
                index != len(self._items)
                or index >= 128
                or not isinstance(item["id"], str)
                or not item["id"]
            ):
                raise UnsupportedResponse("Invalid item identity/index")
            if any(i["id"] == item["id"] for i in self._items.values()):
                raise UnsupportedResponse("Duplicate item identity")
            if item.get("status") != "in_progress":
                raise UnsupportedResponse("Invalid item start status")
            allowed = (
                {"id", "type", "status", "role", "content"}
                if item["type"] == "message"
                else {"id", "type", "status", "call_id", "name", "arguments"}
            )
            if set(item) - allowed:
                raise UnsupportedResponse("Unsupported output item fields")
            self._bytes += len(json.dumps(item).encode())
            if self._bytes > self._limit:
                raise UnsupportedResponse("Native output exceeds limit")
            self._items[index] = item
            self._text[index] = ""
            if item["type"] == "message" and item["role"] == "assistant" and item["content"] == []:
                return [BlockStart(index)]
            if item["type"] == "function_call" and item["arguments"] == "":
                if not all(isinstance(item[k], str) and item[k] for k in ("call_id", "name")):
                    raise UnsupportedResponse("Invalid function identity")
                return [BlockStart(index, call=ToolCall(item["call_id"], item["name"], ""))]
            raise UnsupportedResponse("Unsupported native output item")
        item = self._items[index]
        if index in self._closed or ("item_id" in value and value["item_id"] != item["id"]):
            raise UnsupportedResponse("Closed or mismatched item")
        if "content_index" in value and (
            type(value["content_index"]) is not int or value["content_index"] != 0
        ):
            raise UnsupportedResponse("Multiple content parts unsupported")
        is_text = item["type"] == "message"
        if kind == "response.content_part.added":
            if (
                not is_text
                or index in self._content
                or value["part"]["type"] != "output_text"
                or value["part"]["text"]
            ):
                raise UnsupportedResponse("Invalid content start")
            self._content.add(index)
            return []
        if kind in {"response.output_text.delta", "response.function_call_arguments.delta"}:
            if (
                is_text != (kind == "response.output_text.delta")
                or index in self._text_done
                or (is_text and index not in self._content)
            ):
                raise UnsupportedResponse("Unexpected output delta")
            delta = value["delta"]
            if not isinstance(delta, str):
                raise UnsupportedResponse("Invalid delta")
            self._bytes += len(delta.encode())
            if self._bytes > self._limit:
                raise UnsupportedResponse("Native output exceeds limit")
            self._text[index] += delta
            return [Delta(index, delta)]
        if kind in {"response.output_text.done", "response.function_call_arguments.done"}:
            if is_text != (kind == "response.output_text.done") or index in self._text_done:
                raise UnsupportedResponse("Unexpected output end")
            if value["text" if is_text else "arguments"] != self._text[index]:
                raise UnsupportedResponse("Output end mismatch")
            self._text_done.add(index)
            return []
        if kind == "response.content_part.done":
            part = value["part"]
            if (
                not is_text
                or index not in self._text_done
                or index in self._content_done
                or part["type"] != "output_text"
                or part["text"] != self._text[index]
                or part.get("annotations")
            ):
                raise UnsupportedResponse("Invalid content end")
            self._content_done.add(index)
            return []
        if kind == "response.output_item.done":
            final = value["item"]
            if set(final) != set(item):
                raise UnsupportedResponse("Output item shape changed")
            if final.get("status") not in {"completed", "incomplete"}:
                raise UnsupportedResponse("Invalid item terminal status")
            if (
                index not in self._text_done
                or final["id"] != item["id"]
                or final["type"] != item["type"]
            ):
                raise UnsupportedResponse("Invalid item end")
            if is_text:
                if (
                    index not in self._content_done
                    or final["role"] != "assistant"
                    or len(final["content"]) != 1
                ):
                    raise UnsupportedResponse("Invalid final message")
                part = final["content"][0]
                if (
                    part["type"] != "output_text"
                    or part["text"] != self._text[index]
                    or part.get("annotations")
                ):
                    raise UnsupportedResponse("Final message mismatch")
            elif (
                any(final[k] != item[k] for k in ("call_id", "name"))
                or final["arguments"] != self._text[index]
            ):
                raise UnsupportedResponse("Final tool mismatch")
            self._items[index] = final
            self._closed.add(index)
            return [BlockEnd(index)]
        raise UnsupportedResponse("Unsupported native Responses event")
