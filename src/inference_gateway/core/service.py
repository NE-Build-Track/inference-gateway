import base64
import json
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import replace

from inference_gateway.core.backend import Backend, BackendError, UnsupportedResponse
from inference_gateway.core.models import (
    BlockEnd,
    BlockStart,
    Completion,
    Delta,
    Document,
    Event,
    Finished,
    Generate,
    HostedResult,
    Media,
    Output,
    Reasoning,
    Text,
)
from inference_gateway.core.structured import reject_nonfinite, validate_value


def assemble(start: BlockStart, text: str) -> Output:
    if start.call is not None:
        return replace(start.call, arguments=text)
    if isinstance(start.block, (Text, Reasoning)):
        return replace(start.block, text=text)
    return start.block if start.block is not None else Text(text)


class GenerationService:
    def __init__(self, backend: Backend) -> None:
        self._backend = backend

    async def stream(self, request: Generate) -> AsyncGenerator[Event, None]:
        active: dict[int, BlockStart] = {}
        chunks: dict[int, list[str]] = {}
        completed: dict[int, Output] = {}
        next_index = 0
        done = False
        size = 0
        calls: set[str] = set()
        hosted_pending: set[str] = set()
        async with aclosing(self._backend.stream(request)) as events:
            async for event in events:
                if done:
                    raise BackendError("Event after finish")
                match event:
                    case BlockStart():
                        if active and (
                            event.call is None
                            or event.call.hosted
                            or any(s.call is None or s.call.hosted for s in active.values())
                        ):
                            raise BackendError("Only client tool blocks may overlap")
                        if event.index != next_index or next_index >= 128:
                            raise BackendError("Invalid block start")
                        if event.call is not None and (
                            event.call.arguments or event.block is not None
                        ):
                            raise BackendError("Tool arguments must arrive as deltas")
                        if isinstance(event.block, (Text, Reasoning)) and event.block.text:
                            raise BackendError("Text must arrive as deltas")
                        if event.call:
                            if not event.call.id or event.call.id in calls:
                                raise BackendError("Duplicate or empty tool call ID")
                            calls.add(event.call.id)
                            if event.call.hosted:
                                hosted_pending.add(event.call.id)
                        if isinstance(event.block, HostedResult):
                            if event.block.call_id not in hosted_pending or any(
                                s.call and s.call.id == event.block.call_id for s in active.values()
                            ):
                                raise BackendError("Hosted result without completed call")
                            hosted_pending.remove(event.block.call_id)
                        if isinstance(event.block, Media):
                            media = event.block
                            if (
                                media.is_url
                                or media.media_type not in {"image/png", "audio/wav", "audio/pcm16"}
                                or not media.media_type.startswith(media.kind + "/")
                            ):
                                raise UnsupportedResponse("Unsupported output media format")
                            try:
                                if not base64.b64decode(media.data, validate=True):
                                    raise ValueError("Empty media")
                            except ValueError as exc:
                                raise UnsupportedResponse("Invalid output media") from exc
                        size += len(repr(event).encode())
                        active[event.index], chunks[event.index] = event, []
                        next_index += 1
                    case Delta():
                        start = active.get(event.index)
                        if start is None:
                            raise BackendError("Delta outside active block")
                        if isinstance(start.block, (HostedResult, Media)):
                            raise BackendError("Atomic blocks cannot receive text deltas")
                        size += len(event.text.encode())
                        chunks[event.index].append(event.text)
                    case BlockEnd():
                        start = active.pop(event.index, None)
                        if start is None:
                            raise BackendError("Invalid block end")
                        text = "".join(chunks.pop(event.index))
                        if start.call is not None:
                            try:
                                parsed = json.loads(text, parse_constant=reject_nonfinite)
                            except ValueError as exc:
                                raise BackendError("Invalid tool JSON") from exc
                            if not isinstance(parsed, dict):
                                raise BackendError("Tool arguments must be an object")
                        part = assemble(start, text)
                        if isinstance(part, Text):
                            documents = [
                                p
                                for m in request.messages
                                for p in m.content
                                if isinstance(p, Document)
                            ]
                            for citation in part.citations:
                                source_text = text
                                if citation.document_index is not None:
                                    if not 0 <= citation.document_index < len(documents):
                                        raise UnsupportedResponse("Invalid citation document")
                                    source_text = documents[citation.document_index].text
                                if (
                                    not 0 <= citation.start <= citation.end <= len(source_text)
                                    or source_text[citation.start : citation.end] != citation.text
                                ):
                                    raise UnsupportedResponse("Invalid citation span")
                        completed[event.index] = part
                    case Finished():
                        if active or hosted_pending:
                            raise BackendError("Unclosed block or missing hosted result")
                        if (
                            min(event.input_tokens, event.output_tokens, event.cached_input_tokens)
                            < 0
                            or event.cached_input_tokens > event.input_tokens
                        ):
                            raise BackendError("Invalid usage")
                        if request.output_format is not None and event.reason != "length":
                            try:
                                text = "".join(
                                    p.text
                                    for _, p in sorted(completed.items())
                                    if isinstance(p, Text)
                                )
                                value = json.loads(text, parse_constant=reject_nonfinite)
                                if not isinstance(value, dict):
                                    raise ValueError("Expected JSON object")
                                if request.output_format.schema is not None:
                                    validate_value(value, request.output_format.schema)
                            except (ValueError, TypeError) as exc:
                                raise UnsupportedResponse("Invalid structured output") from exc
                        done = True
                if size > 8 * 1024 * 1024:
                    raise BackendError("Output exceeds gateway limit")
                yield event
        if not done:
            raise BackendError("Backend ended without Finished")

    async def complete(self, request: Generate) -> Completion:
        starts: dict[int, BlockStart] = {}
        chunks: dict[int, list[str]] = {}
        content: dict[int, Output] = {}
        finish: Finished | None = None
        async with aclosing(self.stream(request)) as events:
            async for event in events:
                match event:
                    case BlockStart():
                        starts[event.index], chunks[event.index] = event, []
                    case Delta():
                        chunks[event.index].append(event.text)
                    case BlockEnd():
                        content[event.index] = assemble(
                            starts.pop(event.index), "".join(chunks.pop(event.index))
                        )
                    case Finished():
                        finish = event
        assert finish is not None
        return Completion(tuple(p for _, p in sorted(content.items())), finish)
