"""Exercise the built application with a real engine and a paid-shaped offline transport.

Run inside the image with --network none. This uses fixture credentials, not deployment secrets.
"""

import asyncio
import json
import tempfile
from pathlib import Path

import httpx
from livepeer_builder import EngineSupply
from livepeer_builder.testing import ScriptedStreamTransport, StreamScript, make_engine, runner

from inference_gateway.app import Settings, create_app
from inference_gateway.config import LivepeerSettings, ModelRoute


def frames() -> list[bytes]:
    values = [
        {
            "choices": [
                {"index": 0, "delta": {"content": "offline engine reply"}, "finish_reason": None}
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 3}},
    ]
    return [("data: " + json.dumps(value) + "\n\n").encode() for value in values] + [
        b"data: [DONE]\n\n"
    ]


async def main() -> None:
    options = LivepeerSettings(routes=(ModelRoute(name="offline", app="fixture/chat"),))
    transport = ScriptedStreamTransport(opens=[StreamScript(events=frames()) for _ in range(4)])
    with tempfile.TemporaryDirectory() as directory:
        async with make_engine(
            runners=[runner("fixture/chat")],
            supply=EngineSupply(transport=transport),
            database_url=f"sqlite+aiosqlite:///{Path(directory) / 'engine.db'}",
            allocation_mode="per_application",
            shared_payment_key="fixture-key",
            shared_allocation_id="fixture-allocation",
        ) as engine:

            async def factory(settings):
                return engine

            app = create_app(
                settings=Settings(backend="livepeer"),
                livepeer_settings=options,
                engine_factory=factory,
            )
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://fixture"
                ) as client:
                    assert (await client.get("/readyz")).status_code == 200
                    for route in ["/v1/chat/completions", "/v1/messages"]:
                        for stream in [False, True]:
                            body = {
                                "model": "offline",
                                "messages": [{"role": "user", "content": "hi"}],
                                "stream": stream,
                            }
                            if route == "/v1/messages":
                                body["max_tokens"] = 100
                            result = await client.post(
                                route, json=body, headers={"anthropic-version": "2023-06-01"}
                            )
                            assert result.status_code == 200, result.status_code
                            assert "offline engine reply" in result.text
                            if stream:
                                assert "[DONE]" in result.text or "message_stop" in result.text
                    assert engine.health().running_jobs == 0
                    assert all(stream.closed for stream in transport.streams)
                    assert len(transport.calls) == 4
    print("Offline Livepeer image smoke passed: engine lifecycle, both protocols, JSON and SSE")


if __name__ == "__main__":
    asyncio.run(main())
