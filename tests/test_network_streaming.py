"""Real sockets prove incremental delivery and disconnect cleanup beyond ASGI buffering."""

import asyncio
import socket
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn

from endpoint_gateway.app import Settings, create_app
from endpoint_gateway.core.models import BlockStart, Delta


class PausedBackend:
    def __init__(self):
        self.closed = asyncio.Event()

    async def stream(self, request):
        try:
            yield BlockStart(0)
            yield Delta(0, "first")
            await asyncio.Event().wait()
        finally:
            self.closed.set()


@asynccontextmanager
async def serve(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_first_delta_arrives_before_finish_and_disconnect_closes_backend(protocol):
    backend = PausedBackend()
    path = "/v1/chat/completions" if protocol == "openai" else "/v1/messages"
    body = {"model": "fake", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    if protocol == "anthropic":
        body["max_tokens"] = 100
    async with serve(create_app(backend, Settings(timeout_seconds=20))) as url:
        async with httpx.AsyncClient(base_url=url, timeout=3) as client:
            async with client.stream(
                "POST", path, json=body, headers={"anthropic-version": "2023-06-01"}
            ) as response:
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if '"first"' in line:
                        break
                assert not backend.closed.is_set()
            await asyncio.wait_for(backend.closed.wait(), 3)
