import json
import time
from decimal import Decimal

import httpx
import pytest
from livepeer_builder.testing import FakeEngine, Reply, runner

from inference_gateway.adapters.ledger import SQLiteLedger
from inference_gateway.adapters.runner_contract import PROFILES
from inference_gateway.app import create_app
from inference_gateway.config import LivepeerSettings, ModelRoute, NativeApproval, Settings
from inference_gateway.core.accounting import Price

USAGES = {
    "chat_completions": {"prompt_tokens": 5, "completion_tokens": 3},
    "responses": {"input_tokens": 5, "output_tokens": 3},
    "messages": {"input_tokens": 5, "output_tokens": 3},
}


def setup(endpoint, reply, tmp_path=None, expiry=None):
    meta = {
        "inference_contract": 1,
        "model_name": "m",
        "runtime": {"name": "vllm", "image": "vllm/vllm-openai@sha256:" + "a" * 64},
        "deployment_revision": "sha256:" + "b" * 64,
        "endpoints": {endpoint: PROFILES[endpoint]},
        "limits": {"context_tokens": 4096},
        "admission": "candidate",
    }
    candidate = runner(
        "chat",
        metadata=json.dumps(meta, separators=(",", ":")),
        orchestrator_url="https://orch",
        capacity_available=1,
    ).model_copy(update={"runner_id": "one"})
    engine = FakeEngine(
        streaming=True, runners=[candidate], replies={("chat", PROFILES[endpoint]["path"]): reply}
    )
    options = LivepeerSettings(
        routes=(ModelRoute(name="m", app="chat", mode="native"),),
        native_approvals=(
            NativeApproval(
                orchestrator="https://orch",
                runner_id="one",
                app="chat",
                model="m",
                deployment_revision=meta["deployment_revision"],
                endpoint=endpoint,
                expires_at=expiry or time.time() + 60,
            ),
        ),
    )

    async def factory(_):
        return engine

    ledger = None
    secret = ""
    if tmp_path:
        ledger = SQLiteLedger(str(tmp_path / "retail.db"))
        uid = ledger.create_user("Alice")
        _, secret = ledger.create_key(uid, "test", frozenset({"inference", "reports"}))
        ledger.set_price(Price("v1", "m", "usd", Decimal("1"), Decimal("2")))
    config = Settings(
        backend="livepeer", accounting_db=str(tmp_path / "retail.db") if tmp_path else None
    )
    return (
        create_app(settings=config, livepeer_settings=options, engine_factory=factory),
        engine,
        ledger,
        secret,
    )


@pytest.mark.parametrize("endpoint", list(PROFILES))
async def test_native_http_keeps_bytes_and_accounts(endpoint, tmp_path):
    raw_response = json.dumps(
        {"id": "native", "status": "completed", "usage": USAGES[endpoint], "future": "untouched"},
        indent=2,
    ).encode()
    reply = Reply(
        content_type="application/json",
        chunks=(raw_response[:10], raw_response[10:]),
        headers=(("Content-Type", "application/json"), ("Set-Cookie", "secret")),
    )
    app, engine, ledger, secret = setup(endpoint, reply, tmp_path)
    raw = (
        b'{ "model":"m", "store":false, '
        b'"tools":[{"strict":true,"parameters":{"$ref":"#/$defs/X"}}], "future":42 }'
    )
    headers = {
        "content-type": "application/json",
        "authorization": "Bearer " + secret,
        "x-api-key": secret,
        "anthropic-version": "future-version",
        "anthropic-beta": "future-beta",
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            result = await client.post(PROFILES[endpoint]["path"], content=raw, headers=headers)
            assert result.status_code == 200, result.text
            assert result.content == raw_response and "set-cookie" not in result.headers
            assert (await client.get("/v1/models", headers=headers)).json()["data"][0]["id"] == "m"
    request = engine.jobs.requests[0][1]
    assert request.raw_body == raw and request.payload is None
    assert request.capability == "native.http.v1" and request.return_http_errors
    assert dict(request.headers) == {
        "anthropic-version": "future-version",
        "anthropic-beta": "future-beta",
        "accept": "*/*",
    }
    assert len(engine.jobs.usage_reports) == 1
    row = ledger.rows(ledger.authenticate(secret), 0, time.time() + 1)[0]
    assert (row["input_tokens"], row["output_tokens"]) == (5, 3)
    assert row["retail_amount"] == "0.000011000"
    with ledger.connection() as db:
        assert db.execute("SELECT job_id FROM requests WHERE id=?", (row["id"],)).fetchone()[0]


async def test_native_http_errors_keep_status_and_bytes_without_usage(tmp_path):
    raw = b'{ "error":{"message":"backend specific", "future":true} }'
    app, engine, ledger, secret = setup(
        "responses",
        Reply(status_code=400, content_type="application/json", chunks=(raw,)),
        tmp_path,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/responses",
                content=b'{"model":"m","store":false}',
                headers={"authorization": "Bearer " + secret},
            )
    assert response.status_code == 400 and response.content == raw
    row = ledger.rows(ledger.authenticate(secret), 0, time.time() + 1)[0]
    assert row["input_tokens"] is None
    with ledger.connection() as db:
        assert db.execute("SELECT job_id FROM requests WHERE id=?", (row["id"],)).fetchone()[0]
    assert not engine.jobs.usage_reports


async def test_native_stream_unknown_events_are_unchanged():
    raw = (
        b': native comment\n\ndata: {"choices":[],"future":true}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":3}}\n\n'
        b"data: [DONE]\n\n"
    )
    app, engine, _, _ = setup(
        "chat_completions",
        Reply(content_type="text/event-stream", chunks=tuple(bytes([b]) for b in raw)),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "m", "stream": True, "stream_options": {"include_usage": True}},
            )
    assert response.status_code == 200 and response.content == raw
    assert len(engine.jobs.usage_reports) == 1


@pytest.mark.parametrize(
    "body,status",
    [
        (b'{"model":"m","stream":true}', 400),
        (b'{"model":"m","model":"m"}', 400),
        (b'{"model":"m"}', 503),
    ],
)
async def test_invalid_envelope_or_expired_approval_never_dispatches(body, status):
    app, engine, _, _ = setup("chat_completions", Reply(), expiry=1)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/v1/chat/completions", content=body)
            assert response.status_code == status
    assert not engine.jobs.requests


@pytest.mark.parametrize("streaming", [False, True])
async def test_incomplete_response_keeps_provider_payload_and_measured_usage(tmp_path, streaming):
    data = {"status": "incomplete", "usage": {"input_tokens": 2, "output_tokens": 1}}
    raw = (
        json.dumps({"type": "response.incomplete", "response": data}).encode()
        if streaming
        else json.dumps(data).encode()
    )
    if streaming:
        raw = b"data: " + raw + b"\n\n"
    app, engine, ledger, secret = setup(
        "responses",
        Reply(content_type="text/event-stream" if streaming else "application/json", chunks=(raw,)),
        tmp_path,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/responses",
                json={"model": "m", "store": False, "stream": streaming},
                headers={"authorization": "Bearer " + secret},
            )
    assert response.status_code == 200 and response.content == raw
    row = ledger.rows(ledger.authenticate(secret), 0, time.time() + 1)[0]
    assert row["output_tokens"] == 1
    assert row["outcome"] == "failed"
    assert len(engine.jobs.usage_reports) == 1


async def test_encoded_response_is_not_returned_as_unencoded_json():
    app, _, _, _ = setup(
        "responses", Reply(headers=(("Content-Encoding", "gzip"),), chunks=(b"compressed",))
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            result = await client.post("/v1/responses", json={"model": "m", "store": False})
    assert result.status_code == 502
    assert b"compressed" not in result.content


async def test_authentication_precedes_native_dispatch(tmp_path):
    app, engine, _, _ = setup("chat_completions", Reply(), tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            result = await client.post("/v1/chat/completions", json={"model": "m"})
    assert result.status_code == 401 and not engine.jobs.requests


async def test_native_deadline_before_headers_returns_504():
    import asyncio

    class WaitingBackend:
        cancelled = False

        def accepts(self, model):
            return model == "m"

        def models(self):
            return ("m",)

        async def open(self, request):
            try:
                await asyncio.Future()
            finally:
                self.cancelled = True

    backend = WaitingBackend()
    app = create_app(settings=Settings(timeout_seconds=0.01), native_backend=backend)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post("/v1/chat/completions", json={"model": "m"})
    assert result.status_code == 504 and backend.cancelled


async def test_asgi_disconnect_closes_native_stream_without_fabricated_events():
    import asyncio

    from starlette.responses import JSONResponse

    from inference_gateway.adapters.native_http import NativeHTTPResponse
    from inference_gateway.adapters.native_request import envelope

    disconnect = asyncio.Event()

    class Stream:
        status_code = 200
        headers = (("content-type", "text/event-stream"),)
        job_id = "j"
        closed = False
        disconnected_called = False

        async def __aiter__(self):
            yield b'data: {"choices":[],"future":true}\n\n'
            await asyncio.Future()

        async def complete(self):
            pytest.fail("incomplete stream cannot complete")

        async def disconnected(self):
            self.disconnected_called = True

        async def aclose(self):
            self.closed = True

    stream = Stream()

    class Backend:
        async def open(self, request):
            return stream

    response = NativeHTTPResponse(
        Backend(),
        envelope(
            b'{"model":"m","stream":true,"stream_options":{"include_usage":true}}',
            path="/v1/chat/completions",
            request_id="r",
        ),
        ledger=None,
        deadline_seconds=2,
        failure=lambda message, status: JSONResponse({}, status_code=status),
    )
    sent = []

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body":
            disconnect.set()

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    await response({}, receive, send)
    assert stream.closed and stream.disconnected_called
    assert len([m for m in sent if m["type"] == "http.response.body"]) == 1


@pytest.mark.parametrize("endpoint", list(PROFILES))
async def test_native_routes_work_through_provider_sdks(endpoint):
    from anthropic import AsyncAnthropic
    from openai import AsyncOpenAI

    payloads = {
        "chat_completions": {
            "id": "chatcmpl-native",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "hello"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {**USAGES["chat_completions"], "total_tokens": 8},
        },
        "responses": {
            "id": "resp_native",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "model": "m",
            "output": [
                {
                    "type": "message",
                    "id": "msg_native",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "hello", "annotations": []}],
                }
            ],
            "usage": {**USAGES["responses"], "total_tokens": 8},
        },
        "messages": {
            "id": "msg_native",
            "type": "message",
            "role": "assistant",
            "model": "m",
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "content": [{"type": "text", "text": "hello"}],
            "usage": USAGES["messages"],
        },
    }
    app, engine, _, _ = setup(
        endpoint,
        Reply(content_type="application/json", chunks=(json.dumps(payloads[endpoint]).encode(),)),
    )
    async with app.router.lifespan_context(app):
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        if endpoint == "messages":
            async with AsyncAnthropic(
                base_url="http://test", api_key="local", http_client=http, max_retries=0
            ) as client:
                result = await client.messages.create(
                    model="m",
                    max_tokens=8,
                    messages=[{"role": "user", "content": "hi"}],
                    extra_body={"native_extension": True},
                )
                assert result.content[0].text == "hello"
        else:
            async with AsyncOpenAI(
                base_url="http://test/v1", api_key="local", http_client=http, max_retries=0
            ) as client:
                if endpoint == "responses":
                    result = await client.responses.create(
                        model="m", input="hi", store=False, extra_body={"native_extension": True}
                    )
                    assert result.output_text == "hello"
                else:
                    result = await client.chat.completions.create(
                        model="m",
                        messages=[{"role": "user", "content": "hi"}],
                        extra_body={"native_extension": True},
                    )
                    assert result.choices[0].message.content == "hello"
    assert json.loads(engine.jobs.requests[0][1].raw_body)["native_extension"] is True


@pytest.mark.parametrize("kind,expected", [("timeout", 504), ("no_capacity", 503)])
async def test_engine_failures_keep_timeout_and_availability_status(kind, expected):
    from livepeer_builder.testing import Fails

    app, _, _, _ = setup(
        "chat_completions",
        Fails(kind=kind, message="private backend detail", payment_sent=kind == "timeout"),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/v1/chat/completions", json={"model": "m"})
    assert response.status_code == expected
    assert "private backend detail" not in response.text
