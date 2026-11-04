"""System One forwarding and per-SDK model lists, through the real client libraries."""

import base64
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import httpx
import httpx2
import pytest
from anthropic import AsyncAnthropic
from livepeer_builder.testing import FakeEngine, Reply, runner
from openai import AsyncOpenAI
from typesafe_sdk import (
    AsyncTypeSafeClient,
    Image,
    Noul,
    RetryPolicy,
    TypeSafeBadRequestError,
    TypeSafeInternalServerError,
    TypeSafeUnprocessableEntityError,
)

from inference_gateway.adapters.ledger import SQLiteLedger
from inference_gateway.adapters.native_request import InvalidNativeRequest, envelope
from inference_gateway.adapters.native_usage import UsageObserver
from inference_gateway.adapters.runner_contract import PROFILES
from inference_gateway.app import create_app
from inference_gateway.config import LivepeerSettings, ModelRoute, NativeApproval, Settings
from inference_gateway.core.accounting import Price
from inference_gateway.core.backend import BackendError

CHAT, RESPONSES, MESSAGES, SYSTEM_ONE = "chat_completions", "responses", "messages", "systemone"
GENERATION = (CHAT, RESPONSES, MESSAGES)
PATH = "/v1/systemone"
DECISION = {
    "model": "clef",
    "answers": {"needs_help": {"type": "noul", "noul": 0.91}},
    "usage": {"input_tokens": 160, "output_tokens": 0},
}
REQUEST = {
    "model": "clef",
    "state": "The parcel arrived damaged.",
    "questions": {"needs_help": {"type": "noul", "instructions": "Does the customer need help?"}},
}


def decision_reply(**changes):
    raw = json.dumps({**DECISION, **changes}, indent=1).encode()
    return Reply(content_type="application/json", chunks=(raw[:10], raw[10:]))


@dataclass(frozen=True)
class Supply:
    model: str
    advertised: tuple[str, ...]
    approved: tuple[str, ...] | None = None  # Defaults to everything advertised.
    capacity: int = 1
    expires_at: float | None = None
    validated: bool = False


def gateway(*supply, replies=None, tmp_path=None, prices=None, routes=None):
    runners, approvals = [], []
    for index, item in enumerate(supply):
        runner_id, app = f"runner-{index}", f"app/{item.model}"
        revision = "sha256:" + f"{index:x}" * 64
        metadata = (
            {"model_name": item.model}
            if item.validated
            else {
                "inference_contract": 1,
                "model_name": item.model,
                "runtime": {"name": "sglang", "image": "lmsysorg/sglang@sha256:" + "a" * 64},
                "deployment_revision": revision,
                "endpoints": {name: PROFILES[name] for name in item.advertised},
                "limits": {"context_tokens": 16384},
                "admission": "candidate",
            }
        )
        runners.append(
            runner(
                app,
                metadata=json.dumps(metadata, separators=(",", ":")),
                orchestrator_url="https://orch",
                capacity_available=item.capacity,
            ).model_copy(update={"runner_id": runner_id})
        )
        if item.validated:
            continue
        for endpoint in item.advertised if item.approved is None else item.approved:
            approvals.append(
                NativeApproval(
                    orchestrator="https://orch",
                    runner_id=runner_id,
                    app=app,
                    model=item.model,
                    deployment_revision=revision,
                    endpoint=endpoint,
                    expires_at=item.expires_at or time.time() + 60,
                )
            )
    engine = FakeEngine(
        streaming=True,
        runners=runners,
        replies={
            (f"app/{item.model}", PROFILES[endpoint]["path"]): reply
            for item in supply
            for endpoint, reply in (replies or {}).items()
        },
    )
    options = LivepeerSettings(
        routes=routes
        or tuple(
            ModelRoute(
                name=item.model,
                app=f"app/{item.model}",
                mode="validated" if item.validated else "native",
            )
            for item in supply
        ),
        native_approvals=tuple(approvals),
    )

    async def factory(_):
        return engine

    ledger, secret = None, "unused"
    if tmp_path:
        ledger = SQLiteLedger(str(tmp_path / "retail.db"))
        user = ledger.create_user("Customer")
        _, secret = ledger.create_key(user, "test", frozenset({"inference", "reports"}))
        for model in prices if prices is not None else [item.model for item in supply]:
            ledger.set_price(Price("v-" + model, model, "usd", Decimal("1"), Decimal("2")))
    settings = Settings(
        backend="livepeer", accounting_db=str(tmp_path / "retail.db") if tmp_path else None
    )
    app = create_app(settings=settings, livepeer_settings=options, engine_factory=factory)
    return app, engine, ledger, secret


def http_client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def decisions_sdk(app, secret="unused", retry=None):
    return AsyncTypeSafeClient(
        api_key=secret,
        model="clef",
        base_url="http://test",
        transport=httpx2.ASGITransport(app=app),
        retry=retry or RetryPolicy(max_retries=0),
        compression=False,
    )


def charges(ledger):
    with ledger.connection() as db:
        return db.execute("SELECT count(*) FROM entries WHERE kind='charge'").fetchone()[0]


# -- POST /v1/systemone ---------------------------------------------------------------------------


async def test_sdk_round_trip_forwards_bytes_and_charges_input_only(tmp_path):
    app, engine, ledger, secret = gateway(
        Supply("clef", (SYSTEM_ONE,)), replies={SYSTEM_ONE: decision_reply()}, tmp_path=tmp_path
    )
    async with app.router.lifespan_context(app):
        async with decisions_sdk(app, secret) as client:
            result = await client.system_one(
                state="The parcel arrived damaged.",
                questions={"needs_help": Noul(instructions="Does the customer need help?")},
            )
    assert result.nouls["needs_help"].noul == 0.91
    assert (result.usage.input_tokens, result.usage.output_tokens) == (160, 0)
    request = engine.jobs.requests[0][1]
    assert request.path == PATH and request.capability == "native.http.v1"
    sent = json.loads(request.raw_body)
    assert sent["model"] == "clef" and sent["state"] == "The parcel arrived damaged."
    # Client and credential headers stay at the gateway.
    assert not {k for k, _ in request.headers} & {"authorization", "x-typesafe-sdk"}
    assert engine.jobs.usage_reports[0][1]["prompt_tokens"] == 160
    assert engine.jobs.usage_reports[0][1]["completion_tokens"] == 0
    row = ledger.rows(ledger.authenticate(secret), 0, time.time() + 1)[0]
    assert (row["input_tokens"], row["output_tokens"]) == (160, 0)
    assert row["retail_amount"] == "0.000160000"  # Input rate only.
    assert row["outcome"] == "succeeded" and charges(ledger) == 1
    with ledger.connection() as db:
        protocol, job = db.execute(
            "SELECT protocol, job_id FROM requests WHERE id=?", (row["id"],)
        ).fetchone()
    assert protocol == "systemone" and job


async def test_response_bytes_are_unchanged():
    reply = decision_reply(future="untouched")
    app, _, _, _ = gateway(Supply("clef", (SYSTEM_ONE,)), replies={SYSTEM_ONE: reply})
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            raw = b'{ "model":"clef", "state":"s", "questions":{"q":{"type":"noul"}}, "future":1 }'
            response = await client.post(PATH, content=raw)
    assert response.status_code == 200 and response.content == b"".join(reply.chunks)
    assert response.headers["x-request-id"].startswith("req_")


def inline(raw, media="png"):
    return f"data:image/{media};base64,{base64.b64encode(raw).decode()}"


def bare(raw):
    return base64.b64encode(raw).decode()


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 24
JPEG = b"\xff\xd8\xff\xe0" + b"\0" * 24
GIF = b"GIF89a" + b"\0" * 24
WEBP = b"RIFF\x10\0\0\0WEBPVP8 " + b"\0" * 16
WAV = b"RIFF\x10\0\0\0WAVEfmt " + b"\0" * 16
assert bare(JPEG).startswith("/9j/")  # SGLang reads this form as a path; see the JPEG case below.


@pytest.mark.parametrize(
    "images",
    [
        [inline(PNG)],
        [inline(JPEG, "jpeg")],
        [inline(GIF, "gif")],
        [inline(WEBP, "webp")],
        [{"url": inline(PNG)}],
        [{"url": inline(PNG), "detail": "high"}],
        [bare(PNG)],  # Ollama accepts only this form.
        [bare(JPEG)],  # Inert: no base64 text can leave the nonexistent /9j/ directory.
        [{"url": bare(PNG)}],  # The form SGLang's documentation shows.
        [inline(PNG), inline(JPEG, "jpeg")],
        [],
    ],
)
async def test_inline_images_are_forwarded_unchanged(images):
    app, engine, _, _ = gateway(
        Supply("clef", (SYSTEM_ONE,)), replies={SYSTEM_ONE: decision_reply()}
    )
    raw = json.dumps({**REQUEST, "images": images}, indent=1).encode()
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            response = await client.post(
                PATH, content=raw, headers={"content-type": "application/json"}
            )
    assert response.status_code == 200
    assert engine.jobs.requests[0][1].raw_body == raw


@pytest.mark.parametrize(
    "field",
    [
        {"stream": True},  # Ignored by three backends, which answer JSON the SSE observer rejects.
        # Anything a backend would open or fetch.
        {"images": ["/etc/hostname"]},
        {"images": ["/verify/red.png"]},
        {"images": ["file:///etc/hostname"]},
        {"images": ["http://metadata.internal/"]},
        {"images": ["https://example.com/a.png"]},
        {"images": [{"url": "http://metadata.internal/"}]},
        {"images": [{"url": "/etc/hostname"}]},
        {"images": [inline(PNG), "http://metadata.internal/"]},  # Every entry must qualify.
        {"images": ["/9j/../../etc/hostname"]},  # Starts like a JPEG; `.` is not base64.
        # Lookalikes of an inline image.
        {"images": ["data:image/png,not-base64"]},
        {"images": ["data:text/plain;base64,iVBORw0KGgo="]},
        {"images": ["data:image/svg+xml;base64," + bare(b"<svg xmlns='x'/>" * 2)]},
        # llama.cpp hears audio sent through `images`, whatever the field is called.
        {"images": ["data:audio/wav;base64," + bare(WAV)]},
        {"images": [bare(WAV)]},
        {"images": [inline(WAV)]},
        {"images": [inline(b"not an image at all")]},
        {"images": [bare(b"not an image at all!")]},
        {"images": ["data:image/png;base64,"]},
        {"images": [inline(PNG) + "\n"]},
        {"images": ["DATA:image/png;base64," + bare(PNG)]},
        {"images": [" " + inline(PNG)]},
        {"images": [""]},
        # Shapes no backend documents.
        {"images": inline(PNG)},
        {"images": None},
        {"images": [None]},
        {"images": [7]},
        {"images": [[inline(PNG)]]},
        {"images": [{"detail": "high"}]},
        {"images": [{"url": inline(PNG), "cache": True}]},
        {"images": [{"url": inline(PNG), "detail": 3}]},
        {"images": [{"type": "image_url", "image_url": {"url": inline(PNG)}}]},
        # Fetched by some backends, or ignored by others so the decision would not use them.
        {"videos": ["https://example.com/a.mp4"]},
        {"audio": ["http://metadata.internal/a.wav"]},
        {"files": ["http://metadata.internal/a.png"]},
        {"assets": {"a": {"type": "image", "data_base64": bare(PNG)}}},
        {"media_kwargs": {"fps": 1}},
    ],
)
async def test_unsafe_or_ignored_media_fail_before_dispatch(field):
    app, engine, _, _ = gateway(
        Supply("clef", (SYSTEM_ONE,)), replies={SYSTEM_ONE: decision_reply()}
    )
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            response = await client.post(PATH, json={**REQUEST, **field})
    assert response.status_code == 400 and response.headers["x-should-retry"] == "false"
    assert not engine.jobs.requests


async def test_sdk_extra_body_sends_an_image_the_gateway_forwards(tmp_path):
    app, engine, ledger, secret = gateway(
        Supply("clef", (SYSTEM_ONE,)), replies={SYSTEM_ONE: decision_reply()}, tmp_path=tmp_path
    )
    async with app.router.lifespan_context(app):
        async with decisions_sdk(app, secret) as client:
            result = await client.system_one(
                state="See the photo.",
                questions={"needs_help": Noul(instructions="Is it damaged?")},
                extra_body={"images": [inline(PNG)]},
            )
    assert result.nouls["needs_help"].noul == 0.91
    assert json.loads(engine.jobs.requests[0][1].raw_body)["images"] == [inline(PNG)]
    assert charges(ledger) == 1


async def test_sdk_image_helper_is_refused_because_no_backend_takes_multipart():
    app, engine, _, _ = gateway(
        Supply("clef", (SYSTEM_ONE,)), replies={SYSTEM_ONE: decision_reply()}
    )
    async with app.router.lifespan_context(app):
        async with decisions_sdk(app) as client:
            with pytest.raises(TypeSafeBadRequestError):
                await client.system_one(state=Image(PNG), questions={"q": Noul(instructions="q?")})
    assert not engine.jobs.requests


async def test_explicit_stream_false_is_allowed():
    app, engine, _, _ = gateway(
        Supply("clef", (SYSTEM_ONE,)), replies={SYSTEM_ONE: decision_reply()}
    )
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            response = await client.post(PATH, json={**REQUEST, "stream": False})
    assert response.status_code == 200 and len(engine.jobs.requests) == 1


async def test_compressed_body_fails_before_dispatch():
    app, engine, _, _ = gateway(
        Supply("clef", (SYSTEM_ONE,)), replies={SYSTEM_ONE: decision_reply()}
    )
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            response = await client.post(
                PATH,
                content=b"\x28\xb5\x2f\xfdcompressed",
                headers={"content-type": "application/json", "content-encoding": "zstd"},
            )
    assert response.status_code == 400 and not engine.jobs.requests


async def test_chat_body_never_runs_as_chat_on_this_route():
    chat_body = {"model": "gemma", "messages": [{"role": "user", "content": "hi"}]}
    app, engine, _, _ = gateway(Supply("gemma", (), validated=True))
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            response = await client.post(PATH, json=chat_body)
    assert response.status_code == 400 and not engine.jobs.requests


async def test_fake_backend_has_no_system_one_route():
    app = create_app(settings=Settings())
    async with http_client(app) as client:
        fake = {"model": "fake-model", "messages": [{"role": "user", "content": "hi"}]}
        assert (await client.post(PATH, json=fake)).status_code == 400


async def test_authentication_and_permission_precede_dispatch(tmp_path):
    app, engine, ledger, secret = gateway(
        Supply("clef", (SYSTEM_ONE,)), replies={SYSTEM_ONE: decision_reply()}, tmp_path=tmp_path
    )
    user = ledger.create_user("Reporter")
    _, reports = ledger.create_key(user, "reports", frozenset({"reports"}))
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            assert (await client.post(PATH, json=REQUEST)).status_code == 401
            denied = await client.post(
                PATH, json=REQUEST, headers={"authorization": "Bearer " + reports}
            )
            assert denied.status_code == 403
            # System One clients authenticate like OpenAI; x-api-key is not read here.
            wrong = await client.post(PATH, json=REQUEST, headers={"x-api-key": secret})
            assert wrong.status_code == 401
    assert not engine.jobs.requests


async def test_unpriced_model_fails_before_dispatch(tmp_path):
    app, engine, _, secret = gateway(
        Supply("clef", (SYSTEM_ONE,)),
        replies={SYSTEM_ONE: decision_reply()},
        tmp_path=tmp_path,
        prices=[],
    )
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            response = await client.post(
                PATH, json=REQUEST, headers={"authorization": "Bearer " + secret}
            )
    assert response.status_code == 400 and not engine.jobs.requests


@pytest.mark.parametrize(
    ("reply", "status", "outcome"),
    [
        (decision_reply(), 200, "succeeded"),
        (
            Reply(status_code=422, content_type="application/json", chunks=(b'{"detail":[]}',)),
            422,
            "failed",
        ),
    ],
)
async def test_native_requests_log_their_completion_without_bodies(
    tmp_path, caplog, reply, status, outcome
):
    app, _, _, secret = gateway(
        Supply("clef", (SYSTEM_ONE,)), replies={SYSTEM_ONE: reply}, tmp_path=tmp_path
    )
    caplog.set_level(logging.INFO, logger="inference_gateway")
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            response = await client.post(
                PATH, json=REQUEST, headers={"authorization": "Bearer " + secret}
            )
    assert response.status_code == status
    finished = [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.getMessage().startswith('{"event": "native_request_finished"')
    ]
    assert len(finished) == 1
    record = finished[0]
    assert record.pop("elapsed_s") >= 0
    assert record == {
        "event": "native_request_finished",
        "request_id": response.headers["x-request-id"],
        "endpoint": SYSTEM_ONE,
        "status": status,
        "outcome": outcome,
    }
    assert "parcel" not in caplog.text  # The request body is never logged.


async def test_backend_validation_error_is_preserved_without_charge(tmp_path):
    raw = b'{"detail":[{"loc":["body","questions"],"msg":"Field required","type":"missing"}]}'
    app, _, ledger, secret = gateway(
        Supply("clef", (SYSTEM_ONE,)),
        replies={
            SYSTEM_ONE: Reply(status_code=422, content_type="application/json", chunks=(raw,))
        },
        tmp_path=tmp_path,
    )
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            response = await client.post(
                PATH, json=REQUEST, headers={"authorization": "Bearer " + secret}
            )
        async with decisions_sdk(app, secret) as sdk:
            with pytest.raises(TypeSafeUnprocessableEntityError, match="Field required"):
                await sdk.system_one(state="s", questions={"q": Noul(instructions="q?")})
    assert response.status_code == 422 and response.content == raw
    assert response.headers["x-should-retry"] == "false"
    assert charges(ledger) == 0


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {"input_tokens": 5},
        {"output_tokens": 0},
        {"input_tokens": -1, "output_tokens": 0},
        {"input_tokens": "5", "output_tokens": 0},
    ],
)
async def test_success_without_valid_usage_is_not_charged(tmp_path, usage):
    body = {k: v for k, v in DECISION.items() if k != "usage"}
    if usage is not None:
        body["usage"] = usage
    reply = Reply(content_type="application/json", chunks=(json.dumps(body).encode(),))
    app, engine, ledger, secret = gateway(
        Supply("clef", (SYSTEM_ONE,)), replies={SYSTEM_ONE: reply}, tmp_path=tmp_path
    )
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            response = await client.post(
                PATH, json=REQUEST, headers={"authorization": "Bearer " + secret}
            )
    assert response.status_code == 502 and b"answers" not in response.content
    assert charges(ledger) == 0 and not engine.jobs.usage_reports


async def test_sdk_ignores_should_retry_so_clients_must_disable_retries():
    reply = Reply(status_code=503, content_type="application/json", chunks=(b'{"error":{}}',))
    app, engine, _, _ = gateway(Supply("clef", (SYSTEM_ONE,)), replies={SYSTEM_ONE: reply})
    questions = {"q": Noul(instructions="q?")}
    async with app.router.lifespan_context(app):
        default_like = RetryPolicy(max_retries=2, backoff_initial=0, backoff_max=0)
        async with decisions_sdk(app, retry=default_like) as client:
            with pytest.raises(TypeSafeInternalServerError):
                await client.system_one(state="s", questions=questions)
        assert len(engine.jobs.requests) == 3  # Each attempt is a separate paid job.
        engine.jobs.requests.clear()
        async with decisions_sdk(app) as client:
            with pytest.raises(TypeSafeInternalServerError):
                await client.system_one(state="s", questions=questions)
        assert len(engine.jobs.requests) == 1


# -- Wrong endpoint versus no capacity ------------------------------------------------------------


@pytest.mark.parametrize(
    "supply,path,body",
    [
        (Supply("clef", (SYSTEM_ONE,)), "/v1/chat/completions", {"model": "clef", "messages": []}),
        (Supply("clef", (SYSTEM_ONE,)), "/v1/responses", {"model": "clef", "store": False}),
        (Supply("chat", (CHAT,)), PATH, {"model": "chat", "state": "s", "questions": {}}),
        # Advertised but never approved: the runner's claim does not create support.
        (Supply("clef", (SYSTEM_ONE, CHAT), approved=(SYSTEM_ONE,)), "/v1/chat/completions",
         {"model": "clef", "messages": []}),
    ],
)  # fmt: skip
async def test_unsupported_endpoint_is_400_not_retryable_503(supply, path, body):
    app, engine, _, _ = gateway(supply)
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            response = await client.post(path, json=body)
    assert response.status_code == 400 and not engine.jobs.requests
    assert response.json()["error"]["message"] == "Model is not available on this endpoint"


@pytest.mark.parametrize(
    "supply",
    [
        Supply("clef", (SYSTEM_ONE,), capacity=0),
        Supply("clef", (SYSTEM_ONE,), expires_at=1),
    ],
)
async def test_temporary_unavailability_stays_503(supply):
    app, engine, _, _ = gateway(supply, replies={SYSTEM_ONE: decision_reply()})
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            response = await client.post(PATH, json=REQUEST)
    assert response.status_code == 503 and not engine.jobs.requests


# -- Builder Core's real engine -------------------------------------------------------------------


async def test_real_engine_selects_pays_and_records_a_decision():
    """FakeEngine accepts any path, so prove the real selection and job path accept this one."""
    from livepeer_builder import ActorContext, EngineSupply
    from livepeer_builder.services.discovery import MostCapacityFirst
    from livepeer_builder.testing import ScriptedStreamTransport, StreamScript, make_engine

    from inference_gateway.backends.native_livepeer import LivepeerNativeBackend
    from inference_gateway.backends.native_selection import Admission, NativeSelection
    from inference_gateway.native import forward

    revision = "sha256:" + "b" * 64
    metadata = {
        "inference_contract": 1,
        "model_name": "clef",
        "runtime": {"name": "sglang", "image": "lmsysorg/sglang@sha256:" + "a" * 64},
        "deployment_revision": revision,
        "endpoints": {SYSTEM_ONE: PROFILES[SYSTEM_ONE]},
        "limits": {"context_tokens": 32768},
        "admission": "candidate",
    }
    decision_runner = runner(
        "decide",
        orchestrator_url="https://orch",
        metadata=json.dumps(metadata, separators=(",", ":")),
        capacity_available=1,
    ).model_copy(update={"runner_id": "one"})
    # A chat runner on the same app must never be chosen for a decision.
    chat_runner = runner(
        "decide",
        orchestrator_url="https://orch",
        metadata=json.dumps(
            {**metadata, "endpoints": {CHAT: PROFILES[CHAT]}}, separators=(",", ":")
        ),
        capacity_available=100,
    ).model_copy(update={"runner_id": "chat", "url": "https://chat-runner"})
    admission = Admission("https://orch", "one", "decide", "clef", revision, SYSTEM_ONE, 2e10)
    raw_reply = json.dumps(DECISION).encode()
    transport = ScriptedStreamTransport(
        opens=[StreamScript(events=[raw_reply], status_code=200, content_type="application/json")]
    )
    selection = NativeSelection(MostCapacityFirst(), lambda: (admission,))
    async with make_engine(
        runners=[chat_runner, decision_runner],
        supply=EngineSupply(transport=transport, selection=selection),
        allocation_mode="per_application",
        shared_payment_key="fixture",
        shared_allocation_id="fixture",
    ) as engine:
        options = LivepeerSettings(
            routes=(ModelRoute(name="clef", app="decide", mode="native"),),
        )
        backend = LivepeerNativeBackend(engine, options, lambda: (admission,))
        assert backend.models_for((SYSTEM_ONE,)) == ("clef",)
        assert backend.models_for((CHAT,)) == ()
        body = json.dumps(REQUEST).encode()
        jobs, usage, outcomes, delivered = [], [], [], bytearray()
        async with forward(
            backend,
            envelope(body, path=PATH, request_id="req_real"),
            bind_job=lambda _, job: jobs.append(job),
            record_usage=lambda _, measured: usage.append(measured),
            record_outcome=lambda _, outcome: outcomes.append(outcome),
        ) as delivery:
            async for chunk in delivery.body:
                delivered.extend(chunk)
        assert delivery.status_code == 200 and bytes(delivered) == raw_reply
        assert outcomes == ["succeeded"]
        assert (usage[0].input_tokens, usage[0].output_tokens) == (160, 0)
        assert len(transport.calls) == 1 and transport.calls[0][0] == decision_runner.url
        actor = ActorContext(
            actor_id=options.actor_id, application_id=options.application_id,
            permissions=frozenset({"jobs.read"}),
        )  # fmt: skip
        job = await engine.jobs.get(actor, UUID(jobs[0]))
        assert job.state == "succeeded"
        assert len(job.attempts) == 1 and job.attempts[0].payment_sent
        # Core keeps a zero count rather than treating it as missing usage.
        assert job.usage.quantities["prompt_tokens"] == 160
        assert job.usage.quantities["completion_tokens"] == 0


# -- Envelope and usage units ---------------------------------------------------------------------


@pytest.mark.parametrize("value", [PNG, JPEG, GIF, WEBP])
def test_envelope_accepts_each_supported_image_signature(value):
    body = json.dumps({**REQUEST, "images": [inline(value), bare(value)]}).encode()
    assert envelope(body, path=PATH, request_id="r").endpoint == SYSTEM_ONE


def test_envelope_inspects_only_what_it_must():
    raw = json.dumps({**REQUEST, "future": True}).encode()
    request = envelope(raw, path=PATH, request_id="r")
    assert request.body is raw and request.endpoint == SYSTEM_ONE and not request.streaming
    with pytest.raises(InvalidNativeRequest):
        envelope(json.dumps({**REQUEST, "stream": True}).encode(), path=PATH, request_id="r")
    with pytest.raises(InvalidNativeRequest):
        envelope(b'{"model":"clef","model":"clef"}', path=PATH, request_id="r")


def test_usage_accepts_zero_output_and_requires_both_counts():
    observer = UsageObserver(SYSTEM_ONE, False)
    observer.feed(json.dumps(DECISION).encode())
    finished = observer.finish()
    assert (finished.input_tokens, finished.output_tokens) == (160, 0)
    assert finished.cached_input_tokens == 0
    missing = UsageObserver(SYSTEM_ONE, False)
    missing.feed(b'{"usage":{"input_tokens":1}}')
    with pytest.raises(BackendError):
        missing.finish()


# -- GET /v1/models -------------------------------------------------------------------------------

LIST_SUPPLY = (
    Supply("clef", (SYSTEM_ONE,)),
    Supply("clef-busy", (SYSTEM_ONE,), capacity=0),
    Supply("clef-claims-chat", (SYSTEM_ONE, CHAT), approved=(SYSTEM_ONE,)),
    Supply("clef-unapproved", (SYSTEM_ONE,), approved=()),
    Supply("chat-only", (CHAT,)),
    Supply("full", GENERATION),
)


async def list_openai(app, key="unused"):
    async with http_client(app) as http:
        client = AsyncOpenAI(
            base_url="http://test/v1", api_key=key, http_client=http, max_retries=0
        )
        return [model.id for model in (await client.models.list()).data]


async def list_anthropic(app, key="unused"):
    async with http_client(app) as http:
        client = AsyncAnthropic(
            base_url="http://test", api_key=key, http_client=http, max_retries=0
        )
        return [model.id for model in (await client.models.list()).data]


async def list_system_one(app, key="unused"):
    async with decisions_sdk(app, key) as client:
        return [model.name for model in (await client.models.list()).models]


async def test_each_sdk_sees_only_models_it_can_use():
    app, _, _, _ = gateway(*LIST_SUPPLY)
    async with app.router.lifespan_context(app):
        # chat-only leaves OpenAI's list: that SDK can also call Responses, which it cannot serve.
        assert await list_openai(app) == ["full"]
        assert await list_anthropic(app) == ["full"]
        assert await list_system_one(app) == ["clef", "clef-claims-chat"]


async def test_chat_only_model_stays_callable_by_name():
    raw = json.dumps(
        {
            "id": "c",
            "object": "chat.completion",
            "created": 0,
            "model": "chat-only",
            "choices": [],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
    ).encode()
    reply = Reply(content_type="application/json", chunks=(raw,))
    app, _, _, _ = gateway(Supply("chat-only", (CHAT,)), replies={CHAT: reply})
    async with app.router.lifespan_context(app):
        assert await list_openai(app) == []
        async with http_client(app) as client:
            body = {"model": "chat-only", "messages": [{"role": "user", "content": "hi"}]}
            response = await client.post("/v1/chat/completions", json=body)
    assert response.status_code == 200


async def test_validated_routes_serve_translating_sdks_only():
    app, _, _, _ = gateway(Supply("gemma", (), validated=True))
    async with app.router.lifespan_context(app):
        assert await list_openai(app) == ["gemma"]
        assert await list_anthropic(app) == ["gemma"]
        assert await list_system_one(app) == []


async def test_forgotten_native_mode_cannot_list_a_runner_that_refuses_chat():
    # `mode` defaults to validated, so this is an easy mistake on a Clef route.
    routes = (ModelRoute(name="clef", app="app/clef"),)
    app, engine, _, _ = gateway(Supply("clef", (SYSTEM_ONE,), approved=()), routes=routes)
    async with app.router.lifespan_context(app):
        assert await list_openai(app) == []
        assert await list_anthropic(app) == []
        assert await list_system_one(app) == []
        async with http_client(app) as client:
            body = {"model": "clef", "messages": [{"role": "user", "content": "hi"}]}
            response = await client.post("/v1/chat/completions", json=body)
            assert response.status_code == 503
            assert (await client.get("/readyz")).status_code == 503
    assert not engine.jobs.requests


async def test_validated_route_accepts_a_runner_declaring_its_upstream_endpoint():
    routes = (ModelRoute(name="chat", app="app/chat"),)
    app, _, _, _ = gateway(Supply("chat", (CHAT,), approved=()), routes=routes)
    async with app.router.lifespan_context(app):
        assert await list_openai(app) == ["chat"]
        assert await list_anthropic(app) == ["chat"]


async def test_fake_backend_lists_for_translating_sdks_only():
    app = create_app(settings=Settings())
    assert await list_openai(app) == ["fake-model"]
    assert await list_anthropic(app) == ["fake-model"]
    assert await list_system_one(app) == []


async def test_list_formats_match_each_sdk():
    routes = (
        ModelRoute(
            name="full",
            app="app/full",
            mode="native",
            display_name="Full model",
            description="Generates text.",
            release_date="2026-10-09",
        ),
        ModelRoute(name="clef", app="app/clef", mode="native"),
    )
    app, _, _, _ = gateway(Supply("full", GENERATION), Supply("clef", (SYSTEM_ONE,)), routes=routes)
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            openai_body = (await client.get("/v1/models")).json()
            anthropic_body = (
                await client.get("/v1/models", headers={"anthropic-version": "2023-06-01"})
            ).json()
            unknown = (await client.get("/v1/models", headers={"x-typesafe-sdk": "x"})).json()
        async with http_client(app) as http:
            sdk = AsyncAnthropic(base_url="http://test", api_key="k", http_client=http)
            page = await sdk.models.list()
        async with decisions_sdk(app) as decisions:
            listed = (await decisions.models.list()).models
    assert openai_body == {
        "object": "list",
        "data": [{"id": "full", "object": "model", "created": 0, "owned_by": "livepeer"}],
    }
    assert anthropic_body == {
        "data": [
            {
                "type": "model",
                "id": "full",
                "display_name": "Full model",
                "created_at": "2026-10-09T00:00:00Z",
            }
        ],
        "has_more": False,
        "first_id": "full",
        "last_id": "full",
    }
    assert page.data[0].type == "model" and page.data[0].display_name == "Full model"
    assert page.data[0].created_at == datetime(2026, 10, 9, tzinfo=UTC)
    assert page.has_more is False
    # Unknown details keep the catalog's "unknown" convention instead of being invented.
    assert unknown == {
        "models": [{"name": "clef", "description": "", "release_date": "1970-01-01"}]
    }
    assert listed[0].name == "clef"


async def test_anthropic_list_authenticates_and_fails_like_anthropic(tmp_path):
    app, _, _, secret = gateway(Supply("full", GENERATION), tmp_path=tmp_path)
    version = {"anthropic-version": "2023-06-01"}
    async with app.router.lifespan_context(app):
        assert await list_anthropic(app, secret) == ["full"]
        async with http_client(app) as client:
            bearer = await client.get(
                "/v1/models", headers={**version, "authorization": "Bearer " + secret}
            )
            denied = await client.get("/v1/models", headers={**version, "x-api-key": "wrong"})
    assert bearer.status_code == 200
    assert denied.status_code == 401 and denied.json()["type"] == "error"
    assert denied.headers["x-should-retry"] == "false"


@pytest.mark.parametrize("query", ["limit=5", "after_id=full", "before_id=full"])
async def test_anthropic_pagination_is_rejected_not_ignored(query):
    app, _, _, _ = gateway(Supply("full", GENERATION))
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            response = await client.get(
                "/v1/models?" + query, headers={"anthropic-version": "2023-06-01"}
            )
    assert response.status_code == 400 and response.json()["type"] == "error"


async def test_anthropic_beta_list_query_is_accepted():
    app, _, _, _ = gateway(Supply("full", GENERATION))
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            response = await client.get(
                "/v1/models?beta=true", headers={"anthropic-version": "2023-06-01"}
            )
    assert response.status_code == 200 and response.json()["data"][0]["id"] == "full"


async def test_retail_price_filter_applies_to_every_view(tmp_path):
    app, _, _, secret = gateway(
        Supply("full", GENERATION),
        Supply("unpriced-full", GENERATION),
        Supply("clef", (SYSTEM_ONE,)),
        Supply("unpriced-clef", (SYSTEM_ONE,)),
        tmp_path=tmp_path,
        prices=["full", "clef"],
    )
    async with app.router.lifespan_context(app):
        assert await list_openai(app, secret) == ["full"]
        assert await list_anthropic(app, secret) == ["full"]
        assert await list_system_one(app, secret) == ["clef"]


async def test_list_response_varies_by_detected_sdk():
    app, _, _, _ = gateway(Supply("full", GENERATION))
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            response = await client.get("/v1/models")
    assert response.headers["vary"] == "anthropic-version, x-typesafe-sdk"


async def test_list_errors_also_vary_because_their_envelope_depends_on_the_sdk(tmp_path):
    app, _, _, _ = gateway(Supply("full", GENERATION), tmp_path=tmp_path)
    anthropic = {"anthropic-version": "2023-06-01"}
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            for response in (
                await client.get("/v1/models"),
                await client.get("/v1/models", headers=anthropic),
                await client.get("/v1/models?limit=5", headers=anthropic),
            ):
                assert response.status_code >= 400
                assert response.headers["vary"] == "anthropic-version, x-typesafe-sdk"


def test_validated_selection_skips_a_runner_that_does_not_declare_the_job_path():
    """Listing is not enough: the engine ranks every runner on the route's app at dispatch."""
    from livepeer_builder import JobRequest, NoCapacity
    from livepeer_builder.services.discovery import MostCapacityFirst

    from inference_gateway.backends.discovery import ModelSelection

    def candidate(name, capacity, **fields):
        metadata = json.dumps({"model_name": "m", **fields})
        return runner("app", metadata=metadata, capacity_available=capacity).model_copy(
            update={"runner_id": name}
        )

    decision = candidate("decision", 100, endpoints={SYSTEM_ONE: PROFILES[SYSTEM_ONE]})
    chat = candidate("chat", 1, endpoints={CHAT: PROFILES[CHAT]})
    legacy = candidate("legacy", 1)  # Model-name-only metadata declares nothing, so it stays.
    policy = ModelSelection(MostCapacityFirst())
    job = JobRequest(app="app", model="m", path="/v1/chat/completions")
    assert {r.runner_id for r in policy.order([decision, chat, legacy], job)} == {"chat", "legacy"}
    with pytest.raises(NoCapacity):
        policy.order([decision], job)


async def test_gateway_with_only_decision_models_is_ready():
    app, _, _, _ = gateway(Supply("clef", (SYSTEM_ONE,)))
    async with app.router.lifespan_context(app):
        async with http_client(app) as client:
            assert (await client.get("/readyz")).status_code == 200
