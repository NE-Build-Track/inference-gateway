"""Real sockets prove incremental delivery and disconnect cleanup beyond ASGI buffering."""

import asyncio
import socket
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn

from inference_gateway.app import Settings, create_app
from inference_gateway.core.models import BlockStart, Delta


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


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("mode", ["streaming", "before_headers", "nonstream"])
async def test_livepeer_socket_disconnect_preserves_engine_outcome(protocol, mode, tmp_path):
    import json

    from livepeer_builder import ActorContext, EngineSupply
    from livepeer_builder.testing import ScriptedStreamTransport, StreamScript, make_engine, runner

    from inference_gateway.backends.livepeer import LivepeerBackend
    from inference_gateway.config import LivepeerSettings, ModelRoute

    app_name = "chat/socket-test"
    opened = asyncio.Event()

    class Transport(ScriptedStreamTransport):
        async def open(self, *args, **kwargs):
            stream = await super().open(*args, **kwargs)
            opened.set()
            return stream

    first = b'data: {"choices":[{"index":0,"delta":{"content":"first"},"finish_reason":null}]}\n\n'
    events = [first, 20.0] if mode == "streaming" else [20.0]
    transport = Transport(opens=[StreamScript(events=events)])
    options = LivepeerSettings(routes=(ModelRoute(name="qwen", app=app_name),))
    actor = ActorContext(
        actor_id=options.actor_id,
        application_id=options.application_id,
        permissions=frozenset({"jobs.read"}),
    )
    async with make_engine(
        runners=[runner(app_name, metadata='{"model_name":"qwen"}')],
        supply=EngineSupply(transport=transport),
        allocation_mode="per_application",
        shared_payment_key="fixture-key",
        shared_allocation_id="fixture-allocation",
        stream_settle_s=0.01,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'engine.db'}",
    ) as engine:
        app = create_app(LivepeerBackend(engine, options), Settings(timeout_seconds=30))
        async with serve(app) as url:
            path = "/v1/chat/completions" if protocol == "openai" else "/v1/messages"
            body = {
                "model": "qwen",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": mode != "nonstream",
            }
            if protocol == "anthropic":
                body["max_tokens"] = 100
            if mode == "streaming":
                async with httpx.AsyncClient(base_url=url, timeout=3) as client:
                    async with client.stream(
                        "POST", path, json=body, headers={"anthropic-version": "2023-06-01"}
                    ) as response:
                        assert response.status_code == 200
                        async for line in response.aiter_lines():
                            if '"first"' in line:
                                break
            else:
                port = int(url.rsplit(":", 1)[1])
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                payload = json.dumps(body).encode()
                writer.write(
                    (
                        f"POST {path} HTTP/1.1\r\nHost: localhost\r\n"
                        f"Content-Length: {len(payload)}\r\nContent-Type: application/json\r\n"
                        "anthropic-version: 2023-06-01\r\n\r\n"
                    ).encode()
                    + payload
                )
                await writer.drain()
                await asyncio.wait_for(opened.wait(), 3)
                writer.close()
                await writer.wait_closed()
            # The engine finalizes in its own task; wait for its record and slot release.
            for _ in range(200):
                jobs = await engine.jobs.list_jobs(actor)
                if jobs and jobs[0].state != "running" and engine.health().running_jobs == 0:
                    break
                await asyncio.sleep(0.01)
            assert jobs[0].state == "uncertain"
            if mode == "streaming":
                assert jobs[0].failure.kind == "abandoned"
            else:
                # Before JobStream exists, Builder Core records cancellation as runner_error.
                # After it exists, disconnect handling records abandonment.
                assert jobs[0].failure.kind in {"abandoned", "runner_error"}
            assert jobs[0].failure.payment_sent
            assert transport.streams[0].closed
            assert len(transport.calls) == 1
            assert engine.health().running_jobs == 0
