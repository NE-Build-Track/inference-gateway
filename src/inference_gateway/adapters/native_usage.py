"""Bounded read-only usage extraction. No generation content is reconstructed."""

import json
from typing import Any

from inference_gateway.backends.sse import SSEDecoder
from inference_gateway.core.backend import BackendError
from inference_gateway.core.models import Finished


def _count(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise BackendError("Invalid native usage")
    return value


def usage(endpoint: str, data: dict[str, Any]) -> Finished:
    u = data["usage"]
    if endpoint == "chat_completions":
        i, o = _count(u["prompt_tokens"]), _count(u["completion_tokens"])
        cached = _count(u.get("prompt_tokens_details", {}).get("cached_tokens", 0))
    elif endpoint == "responses":
        i, o = _count(u["input_tokens"]), _count(u["output_tokens"])
        cached = _count(u.get("input_tokens_details", {}).get("cached_tokens", 0))
    else:
        cached = _count(u.get("cache_read_input_tokens", 0))
        i = _count(u["input_tokens"]) + cached + _count(u.get("cache_creation_input_tokens", 0))
        o = _count(u["output_tokens"])
    if cached > i:
        raise BackendError("Invalid cached native usage")
    return Finished("stop", i, o, cached)


class UsageObserver:
    def __init__(self, endpoint: str, streaming: bool, limit: int = 1048576) -> None:
        if endpoint not in {"chat_completions", "responses", "messages"}:
            raise ValueError("Unknown endpoint")
        self.endpoint = endpoint
        self.streaming = streaming
        self.limit = limit
        self.sse = SSEDecoder(limit)
        self.buffer = bytearray()
        self.measured: Finished | None = None
        self.terminal = False
        self.failed = False
        self.initial: dict[str, Any] | None = None
        self.message_delta = False

    def feed(self, chunk: bytes) -> bytes:
        """Return the original object; unknown events remain untouched in delivery."""
        try:
            if not self.streaming:
                if len(self.buffer) + len(chunk) > self.limit:
                    raise BackendError("Native response exceeds observation limit")
                self.buffer.extend(chunk)
            else:
                for frame in self.sse.feed(chunk):
                    self._event(frame)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
            raise BackendError("Invalid native usage framing") from exc
        return chunk

    def _event(self, frame: str) -> None:
        if self.terminal:
            raise BackendError("Data after native terminal event")
        if self.endpoint == "chat_completions" and frame == "[DONE]":
            self.terminal = True
            return
        data = json.loads(frame)
        if not isinstance(data, dict):
            raise BackendError("Invalid native event")
        if self.endpoint == "chat_completions":
            if data.get("error") is not None:
                self.failed = True
            if data.get("usage") is not None:
                self._measure(usage(self.endpoint, data))
        elif self.endpoint == "responses":
            if data.get("type") in {"response.completed", "response.incomplete", "response.failed"}:
                response = data["response"]
                if response.get("usage") is not None:
                    self._measure(usage(self.endpoint, response))
                self.failed = data["type"] != "response.completed"
                self.terminal = True
            elif data.get("type") == "error":
                self.failed = True
        else:
            kind = data.get("type")
            if kind == "message_start":
                if self.initial is not None:
                    raise BackendError("Duplicate message start")
                self.initial = data["message"]["usage"]
            elif kind == "message_delta" and data.get("usage") is not None:
                if self.initial is None or self.message_delta:
                    raise BackendError("Invalid message usage sequence")
                # Input evidence is from message_start; output count is cumulative.
                self._measure(
                    usage(
                        "messages",
                        {
                            "usage": {
                                **self.initial,
                                "output_tokens": data["usage"]["output_tokens"],
                            }
                        },
                    )
                )
                self.message_delta = True
            elif kind == "message_stop":
                self.terminal = True
            elif kind == "error":
                self.failed = True

    def _measure(self, value: Finished) -> None:
        if self.measured is not None:
            raise BackendError("Duplicate native usage")
        self.measured = value

    def finish(self, *, require_success: bool = True) -> Finished | None:
        try:
            if self.streaming:
                self.sse.finish()
            else:
                data = json.loads(self.buffer)
                if self.endpoint == "responses":
                    if data.get("status") not in {"completed", "incomplete", "failed", "cancelled"}:
                        raise BackendError("Unexpected stateless response status")
                    self.failed = data["status"] != "completed"
                if not self.failed or data.get("usage") is not None:
                    self._measure(usage(self.endpoint, data))
                self.terminal = True
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
            raise BackendError("Invalid native usage framing") from exc
        if (
            not self.terminal
            or (self.measured is None and not self.failed)
            or (require_success and self.failed)
        ):
            raise BackendError("Native response lacks successful terminal usage")
        return self.measured
