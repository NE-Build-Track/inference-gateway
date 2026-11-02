"""Conversational adapter for the local ComfyStream capability client."""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from inference_gateway.core.backend import (
    BackendDeadline,
    BackendError,
    UnknownModel,
    UnsupportedFeature,
)
from inference_gateway.core.models import (
    BlockEnd,
    BlockStart,
    Delta,
    Event,
    Finished,
    Generate,
    Sampling,
    Text,
)


class ComfyStreamBackend:
    def __init__(self, client: Any, *, timeout: float = 300):
        self.client = client
        self.timeout = timeout

    def validated_models(self) -> tuple[str, ...]:
        return ("Qwen/Qwen3.8-27B",)

    async def stream(self, request: Generate) -> AsyncGenerator[Event, None]:
        if request.model not in self.validated_models():
            raise UnknownModel()
        if (
            request.tools
            or request.reasoning
            or request.reasoning_effort
            or request.reasoning_budget
            or request.output_format
            or request.output_media
            or request.hosted_search
            or request.sources
            or request.sampling != Sampling()
            or request.temperature is not None
            or request.parallel_tools
            or request.max_tokens > 8192
            or not request.output_text
        ):
            raise UnsupportedFeature()
        messages = []
        for message in request.messages:
            if any(not isinstance(part, Text) for part in message.content):
                raise UnsupportedFeature()
            messages.append(
                {
                    "role": "system" if message.role == "developer" else message.role,
                    "content": "".join(
                        part.text for part in message.content if isinstance(part, Text)
                    ),
                }
            )
        try:
            result = await self.client.execute(
                "qwen-chat",
                {"messages": messages, "max_tokens": request.max_tokens},
                timeout=self.timeout,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
            raise BackendDeadline() from error
        except Exception as error:
            raise BackendError() from error
        usage = result.get("usage")
        if not isinstance(usage, dict) or not all(
            type(usage.get(key)) is int and usage[key] >= 0
            for key in ("input_tokens", "output_tokens")
        ):
            from inference_gateway.core.backend import UnsupportedResponse

            raise UnsupportedResponse("Missing measured token usage")
        yield BlockStart(0)
        yield Delta(0, result["text"])
        yield BlockEnd(0)
        yield Finished(
            "length" if usage["output_tokens"] >= request.max_tokens else "stop",
            usage["input_tokens"],
            usage["output_tokens"],
        )
