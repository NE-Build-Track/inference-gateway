"""Close owned generators even if ASGI fails before the response body starts."""

from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping

import anyio
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send


class ManagedStreamingResponse(StreamingResponse):
    def __init__(
        self,
        content: AsyncGenerator[str, None],
        close: Callable[[], Awaitable[None]],
        *,
        headers: Mapping[str, str],
    ) -> None:
        super().__init__(content, media_type="text/event-stream", headers=headers)
        self._content = content
        self._close = close

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await self._content.aclose()
                finally:
                    await self._close()
