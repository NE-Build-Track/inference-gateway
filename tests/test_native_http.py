import json
import time
from decimal import Decimal

import httpx
import pytest
from contract_helpers import approval, envelope, policy
from livepeer_builder.testing import Fails, FakeEngine, Reply, runner

from inference_gateway.adapters.ledger import SQLiteLedger
from inference_gateway.adapters.native_request import SURFACES
from inference_gateway.app import create_app
from inference_gateway.config import LivepeerSettings, ModelRoute, Settings
from inference_gateway.core.accounting import Price

PATHS = {endpoint: path for path, endpoint in SURFACES.items()}
REVISION = "sha256:" + "b" * 64
# The smallest body each entry's request schema accepts.
BODIES = {
    "chat_completions": {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
    "responses": {"model": "m", "store": False, "input": "hi"},
    "messages": {"model": "m", "max_tokens": 16, "messages": [{"role": "user", "content": "hi"}]},
    "systemone": {"model": "m", "state": "s", "questions": {"q": {"type": "noul"}}},
}

USAGES = {
    "chat_completions": {"prompt_tokens": 5, "completion_tokens": 3},
    "responses": {"input_tokens": 5, "output_tokens": 3},
    "messages": {"input_tokens": 5, "output_tokens": 3},
    "systemone": {"input_tokens": 5, "output_tokens": 3},
}
# System One has its own contract and rejects streaming and images; see test_system_one.py.
GENERATION = [endpoint for endpoint in PATHS if endpoint != "systemone"]


def setup(endpoint, reply, tmp_path=None, expiry=None, metadata_identity=False):
    capability = "decision" if endpoint == "systemone" else "chat"
    meta = envelope(
        "m",
        (endpoint,),
        revision=REVISION,
        runner_id="one" if metadata_identity else None,
        runtime={"name": "vllm", "image": "sha256:" + "a" * 64},
        limits={"context_tokens": 4096},
    )
    candidate = runner(
        capability, metadata=meta, orchestrator_url="https://orch", capacity_available=1
    ).model_copy(update={"runner_id": None if metadata_identity else "one"})
    engine = FakeEngine(
        streaming=True, runners=[candidate], replies={(capability, PATHS[endpoint]): reply}
    )
    options = LivepeerSettings(
        routes=(ModelRoute(name="m", capability=capability, mode="native"),),
    )
    runner_policy = policy(
        approval(
            "https://orch",
            "one",
            "m",
            endpoint,
            revision=REVISION,
            expires_at=expiry or time.time() + 60,
        )
    )

    async def factory(_settings, _policy):
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
        create_app(
            settings=config,
            livepeer_settings=options,
            engine_factory=factory,
            runner_policy=runner_policy,
        ),
        engine,
        ledger,
        secret,
    )


@pytest.mark.parametrize("endpoint", list(PATHS))
@pytest.mark.parametrize("metadata_identity", [False, True])
async def test_native_http_keeps_bytes_and_accounts(endpoint, tmp_path, metadata_identity):
    raw_response = json.dumps(
        {"id": "native", "status": "completed", "usage": USAGES[endpoint], "future": "untouched"},
        indent=2,
    ).encode()
    reply = Reply(
        content_type="application/json",
        chunks=(raw_response[:10], raw_response[10:]),
        headers=(("Content-Type", "application/json"), ("Set-Cookie", "secret")),
    )
    app, engine, ledger, secret = setup(
        endpoint, reply, tmp_path, metadata_identity=metadata_identity
    )
    # Unknown fields reach the runner unchanged where the entry allows them; System One's doesn't.
    extension = {} if endpoint == "systemone" else {"future": 42}
    raw = json.dumps({**BODIES[endpoint], **extension}, indent=1).encode()
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
            result = await client.post(PATHS[endpoint], content=raw, headers=headers)
            assert result.status_code == 200, result.text
            assert result.content == raw_response and "set-cookie" not in result.headers
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


@pytest.mark.parametrize("image_input", [False, True])
async def test_native_http_errors_keep_status_and_bytes_without_usage(tmp_path, image_input):
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
                content=(
                    b'{"model":"m","store":false,"input":[{"role":"user","content":'
                    b'[{"type":"input_image","image_url":"data:image/png;base64,broken"}]}]}'
                    if image_input
                    else b'{"model":"m","store":false,"input":"hi"}'
                ),
                headers={"authorization": "Bearer " + secret},
            )
    assert response.status_code == 400 and response.content == raw
    row = ledger.rows(ledger.authenticate(secret), 0, time.time() + 1)[0]
    assert row["input_tokens"] is None
    with ledger.connection() as db:
        assert db.execute("SELECT job_id FROM requests WHERE id=?", (row["id"],)).fetchone()[0]
    with ledger.connection() as db:
        assert db.execute("SELECT count(*) FROM entries WHERE kind='charge'").fetchone()[0] == 0
    assert not engine.jobs.usage_reports


@pytest.mark.parametrize("status", [400, 429, 502, 503])
@pytest.mark.parametrize("streaming", [False, True])
async def test_forwarded_native_errors_disable_sdk_retries(status, streaming):
    raw = b'{"error":{"message":"busy"}}'
    app, _, _, _ = setup(
        "chat_completions",
        Reply(status_code=status, content_type="application/json", chunks=(raw,)),
    )
    body = {**BODIES["chat_completions"], "stream": streaming}
    if streaming:
        body["stream_options"] = {"include_usage": True}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/v1/chat/completions", json=body)
    # SDKs retry 429/5xx by default; each retry would be a new paid request.
    assert response.status_code == status and response.content == raw
    assert response.headers["x-should-retry"] == "false"


async def test_native_exhausted_allowance_is_insufficient_quota():
    """A native job whose payment the signer refused for a spent allowance (Core D58) never
    started: 429 insufficient_quota, not 503."""
    app, _, _, _ = setup(
        "chat_completions",
        Fails(
            kind="payment",
            message="upstream private secret",
            status_code=402,
            payment_sent=False,
            reason="allowance_exhausted",
        ),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/v1/chat/completions", json=BODIES["chat_completions"])
    assert response.status_code == 429
    assert response.headers["x-should-retry"] == "false"
    error = response.json()["error"]
    assert (error["type"], error["code"]) == ("insufficient_quota", "insufficient_quota")
    assert error["message"] == "Generation allowance exhausted"
    assert "upstream private secret" not in response.text


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_default_sdk_retries_do_not_repeat_forwarded_native_error(protocol):
    from anthropic import APIStatusError as AnthropicStatusError
    from anthropic import AsyncAnthropic
    from openai import APIStatusError as OpenAIStatusError
    from openai import AsyncOpenAI

    endpoint = "chat_completions" if protocol == "openai" else "messages"
    reply = Reply(status_code=503, content_type="application/json", chunks=(b'{"error":{}}',))
    app, engine, _, _ = setup(endpoint, reply)
    messages = [{"role": "user", "content": "hello"}]
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
            if protocol == "openai":
                client = AsyncOpenAI(base_url="http://test/v1", api_key="local", http_client=http)
                with pytest.raises(OpenAIStatusError):
                    await client.chat.completions.create(model="m", messages=messages)
            else:
                claude = AsyncAnthropic(base_url="http://test", api_key="local", http_client=http)
                with pytest.raises(AnthropicStatusError):
                    await claude.messages.create(model="m", max_tokens=50, messages=messages)
    assert len(engine.jobs.requests) == 1


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
                json={
                    **BODIES["chat_completions"],
                    "stream": True,
                    "stream_options": {"include_usage": True},
                },
            )
    assert response.status_code == 200 and response.content == raw
    assert "x-should-retry" not in response.headers
    assert len(engine.jobs.usage_reports) == 1


@pytest.mark.parametrize(
    "body,status,expiry",
    [
        (b'{"model":"m","messages":[],"stream":true}', 400, 1),
        (b'{"model":"m","model":"m"}', 400, 1),
        # Breaks the entry's request schema: no messages.
        (b'{"model":"m"}', 400, None),
        (json.dumps(BODIES["chat_completions"]).encode(), 503, 1),
    ],
)
async def test_invalid_request_or_expired_approval_never_dispatches(body, status, expiry):
    app, engine, _, _ = setup("chat_completions", Reply(), expiry=expiry)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/v1/chat/completions", content=body)
            assert response.status_code == status
    assert not engine.jobs.requests


async def test_a_schema_refusal_names_the_problem_and_never_quotes_the_request():
    app, engine, _, _ = setup("chat_completions", Reply())
    secret = "a-customers-private-path"
    image = {"type": "image_url", "image_url": {"url": f"https://example.com/{secret}.png"}}
    body = {**BODIES["chat_completions"], "messages": [{"role": "user", "content": [image]}]}
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/v1/chat/completions", json=body)
    assert response.status_code == 400
    assert "at /messages/0/content/0/image_url/url: doesn't match" in response.text
    assert secret not in response.text
    assert not engine.jobs.requests


async def test_a_coding_agents_custom_tool_reaches_the_runner():
    """The v2 entry passes tool types it doesn't describe; the runner decides."""
    answer = {
        "id": "chatcmpl_native",
        "object": "chat.completion",
        "created": 1,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hello"},
                "finish_reason": "stop",
            }
        ],
        "usage": USAGES["chat_completions"],
    }
    reply = Reply(content_type="application/json", chunks=(json.dumps(answer).encode(),))
    app, engine, _, _ = setup("chat_completions", reply)
    tools = [{"type": "custom", "custom": {"name": "apply_patch"}}, {"type": "web_search"}]
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/chat/completions", json={**BODIES["chat_completions"], "tools": tools}
            )
    assert response.status_code == 200
    assert len(engine.jobs.requests) == 1


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
                json={**BODIES["responses"], "stream": streaming},
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
            result = await client.post("/v1/responses", json=BODIES["responses"])
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

        def supports(self, model, endpoint):
            return True

        def problems(self, request):
            return ()

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


@pytest.mark.parametrize("endpoint", GENERATION)
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


@pytest.mark.parametrize(
    "kind,paid,expected",
    [
        ("timeout", True, 504),
        ("no_capacity", False, 503),
        ("unreachable", False, 503),
        ("unreachable", True, 502),  # uncertain: may have run and been charged, never 503
    ],
)
async def test_engine_failures_keep_timeout_and_availability_status(kind, paid, expected):
    from livepeer_builder.testing import Fails

    app, _, _, _ = setup(
        "chat_completions",
        Fails(kind=kind, message="private backend detail", payment_sent=paid),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post("/v1/chat/completions", json=BODIES["chat_completions"])
    assert response.status_code == expected
    assert "private backend detail" not in response.text


@pytest.mark.parametrize("endpoint", GENERATION)
@pytest.mark.parametrize("streaming", [False, True])
async def test_native_image_input_preserves_bytes_and_accounts(endpoint, streaming, tmp_path):
    image = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8"
        "AAwMCAO+jP9sAAAAASUVORK5CYII="
    )
    data_url = "data:image/png;base64," + image
    content = [{"type": "text", "text": "Describe the image."}]
    body = {"model": "m", "stream": streaming}
    if endpoint == "responses":
        content = [{"type": "input_text", "text": "Describe the image."}]
        content.append({"type": "input_image", "image_url": data_url})
        body.update(input=[{"role": "user", "content": content}], store=False)
    else:
        content.append(
            {"type": "image_url", "image_url": {"url": data_url}}
            if endpoint == "chat_completions"
            else {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": image},
            }
        )
        body.update(messages=[{"role": "user", "content": content}], max_tokens=32)
        if endpoint == "chat_completions" and streaming:
            body["stream_options"] = {"include_usage": True}
    terminal = {"status": "completed", "usage": USAGES[endpoint]}
    response = json.dumps(terminal).encode()
    if streaming:
        events = {
            "chat_completions": [terminal],
            "responses": [{"type": "response.completed", "response": terminal}],
            "messages": [
                {
                    "type": "message_start",
                    "message": {"usage": {"input_tokens": 5, "output_tokens": 0}},
                },
                {"type": "message_delta", "usage": {"output_tokens": 3}},
                {"type": "message_stop"},
            ],
        }[endpoint]
        # Anthropic's entry tells events apart by their SSE event type, as its API sends them.
        response = b"".join(
            (f"event: {event['type']}\n".encode() if endpoint == "messages" else b"")
            + b"data: "
            + json.dumps(event).encode()
            + b"\n\n"
            for event in events
        )
        if endpoint == "chat_completions":
            response += b"data: [DONE]\n\n"
    app, engine, ledger, secret = setup(
        endpoint,
        Reply(
            content_type="text/event-stream" if streaming else "application/json",
            chunks=(response,),
        ),
        tmp_path,
    )
    raw = json.dumps(body, indent=2).encode()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            result = await client.post(
                PATHS[endpoint],
                content=raw,
                headers={
                    "authorization": "Bearer " + secret,
                    "x-api-key": secret,
                    "content-type": "application/json",
                },
            )
    assert result.status_code == 200
    assert result.content == response
    assert engine.jobs.requests[0][1].raw_body == raw
    assert len(engine.jobs.usage_reports) == 1
    rows = ledger.rows(ledger.authenticate(secret), 0, time.time() + 1)
    assert len(rows) == 1
    assert (rows[0]["input_tokens"], rows[0]["output_tokens"]) == (5, 3)
    assert rows[0]["retail_amount"] == "0.000011000"
