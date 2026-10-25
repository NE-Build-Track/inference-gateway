import json
from collections.abc import AsyncGenerator
from contextlib import aclosing

from endpoint_gateway.core.backend import Backend, BackendError
from endpoint_gateway.core.models import (
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


class GenerationService:
    def __init__(self, backend: Backend) -> None:
        self._backend = backend

    async def stream(self, request: Generate) -> AsyncGenerator[Event, None]:
        active: BlockStart | None = None
        next_index = 0
        done = False
        arguments = ""
        async with aclosing(self._backend.stream(request)) as events:
            async for event in events:
                if done:
                    raise BackendError("Event after finish")
                match event:
                    case BlockStart():
                        if active is not None or event.index != next_index:
                            raise BackendError("Invalid block start")
                        if event.call is not None and event.call.arguments:
                            raise BackendError("Tool arguments must arrive as deltas")
                        active, arguments = event, ""
                    case Delta():
                        if active is None or event.index != active.index:
                            raise BackendError("Delta outside active block")
                        if active.call is not None:
                            arguments += event.text
                    case BlockEnd():
                        if active is None or event.index != active.index:
                            raise BackendError("Invalid block end")
                        if active.call is not None:
                            try:
                                parsed = json.loads(arguments)
                            except ValueError as exc:
                                raise BackendError("Invalid tool JSON") from exc
                            if not isinstance(parsed, dict):
                                raise BackendError("Tool arguments must be an object")
                        active = None
                        next_index += 1
                    case Finished():
                        if active is not None:
                            raise BackendError("Unclosed block")
                        done = True
                yield event
        if not done:
            raise BackendError("Backend ended without Finished")

    async def complete(self, request: Generate) -> Completion:
        content: list[Text | ToolCall] = []
        call: ToolCall | None = None
        chunks: list[str] = []
        finish: Finished | None = None
        async with aclosing(self.stream(request)) as events:
            async for event in events:
                match event:
                    case BlockStart():
                        call, chunks = event.call, []
                    case Delta():
                        chunks.append(event.text)
                    case BlockEnd():
                        text = "".join(chunks)
                        content.append(
                            Text(text) if call is None else ToolCall(call.id, call.name, text)
                        )
                    case Finished():
                        finish = event
        assert finish is not None
        return Completion(tuple(content), finish)
