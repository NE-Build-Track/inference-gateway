import json
from dataclasses import replace

import pytest
from livepeer_builder import JobRequest, NoCapacity
from livepeer_builder.services.discovery import MostCapacityFirst
from livepeer_builder.testing import runner

from inference_gateway.adapters.native_request import InvalidNativeRequest, envelope
from inference_gateway.adapters.native_usage import UsageObserver
from inference_gateway.adapters.runner_contract import PROFILES
from inference_gateway.backends.native_selection import Admission, NativeSelection
from inference_gateway.core.backend import BackendError
from inference_gateway.native import forward


def request(body=b'{ "model":"m", "unknown":{"future":true}, "stream":false }', **kw):
    return envelope(body, path="/v1/chat/completions", request_id="r", **kw)


def test_envelope_retains_bytes_and_filters_credentials():
    raw = b'{ "model":"m", "tools":[{"strict":true,"parameters":{"$ref":"#/$defs/X"}}] }'
    value = request(
        raw,
        headers=[
            ("Authorization", "secret"),
            ("X-Api-Key", "secret"),
            ("Livepeer-Session", "secret"),
            ("Anthropic-Beta", "future"),
            ("Content-Type", "application/json"),
        ],
    )
    assert value.body is raw
    assert value.headers == (("anthropic-beta", "future"), ("content-type", "application/json"))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"model":"m","model":"n"}',
        b'{"model":"m","stream":1}',
        b'{"model":"m","stream":true}',
        b'{"model":"m","x":NaN}',
        b"[]",
        b'{"model":" m"}',
    ],
)
def test_ambiguous_or_unmetered_envelopes_fail(raw):
    with pytest.raises(InvalidNativeRequest):
        request(raw)


def test_explicit_stateless_responses_and_stream_usage():
    body = b'{"model":"m","store":false,"new_field":42}'
    assert envelope(body, path="/v1/responses", request_id="r").body is body
    for extra in [
        {},
        {"store": True},
        {"store": False, "previous_response_id": "x"},
        {"store": False, "background": True},
    ]:
        with pytest.raises(InvalidNativeRequest):
            envelope(
                json.dumps({"model": "m", **extra}).encode(), path="/v1/responses", request_id="r"
            )
    assert request(b'{"model":"m","stream":true,"stream_options":{"include_usage":true}}').streaming
    with pytest.raises(InvalidNativeRequest):
        request(query=b"api_key=secret")
    assert (
        request(headers=[("Connection", "anthropic-beta"), ("Anthropic-Beta", "x")]).headers == ()
    )


def supply():
    meta = {
        "inference_contract": 1,
        "model_name": "m",
        "runtime": {"name": "vllm", "image": "vllm/vllm-openai@sha256:" + "a" * 64},
        "deployment_revision": "sha256:" + "b" * 64,
        "endpoints": {"chat_completions": PROFILES["chat_completions"]},
        "limits": {"context_tokens": 4096},
        "admission": "candidate",
    }
    r = runner(
        "app",
        orchestrator_url="https://orch",
        metadata=json.dumps(meta, separators=(",", ":")),
        capacity_available=1,
    ).model_copy(update={"runner_id": "one"})
    approval = Admission(
        "https://orch", "one", "app", "m", meta["deployment_revision"], "chat_completions", 200
    )
    return r, approval


def test_selection_requires_external_matching_current_evidence():
    r, approval = supply()
    active = [approval]
    policy = NativeSelection(MostCapacityFirst(), lambda: tuple(active), now=lambda: 100)
    job = JobRequest(app="app", model="m", path="/v1/chat/completions")
    assert policy.order([r], job) == [r]
    for change in [
        {"expires_at": 100},
        {"model": "other"},
        {"endpoint": "responses"},
        {"orchestrator": "https://elsewhere"},
        {"deployment_revision": "sha256:" + "c" * 64},
        {"runner_id": "two"},
        {"app": "other"},
    ]:
        active[:] = [replace(approval, **change)]
        with pytest.raises(NoCapacity):
            policy.order([r], job)
    active.clear()
    with pytest.raises(NoCapacity):
        policy.order([r], job)
    active[:] = [approval]
    for change in [{"metadata": "{}"}, {"capacity_available": 0}, {"capacity_available": None}]:
        with pytest.raises(NoCapacity):
            policy.order([r.model_copy(update=change)], job)
    with pytest.raises(NoCapacity):
        policy.order([r], job.model_copy(update={"path": "/admin"}))


def sse(data):
    return b"data: " + json.dumps(data).encode() + b"\r\n\r\n"


CHAT = (
    sse(
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 3,
                "prompt_tokens_details": {"cached_tokens": 2},
            },
        }
    )
    + b"data: [DONE]\n\n"
)
RESPONSES = sse(
    {
        "type": "response.completed",
        "response": {
            "usage": {
                "input_tokens": 5,
                "output_tokens": 3,
                "input_tokens_details": {"cached_tokens": 2},
            }
        },
    }
)
MESSAGES = (
    sse(
        {
            "type": "message_start",
            "message": {
                "usage": {
                    "input_tokens": 2,
                    "cache_read_input_tokens": 2,
                    "cache_creation_input_tokens": 1,
                    "output_tokens": 1,
                }
            },
        }
    )
    + sse({"type": "content_block_delta", "delta": {"future": "é"}})
    + sse({"type": "message_delta", "usage": {"output_tokens": 3}})
    + sse({"type": "message_stop"})
)


@pytest.mark.parametrize(
    "endpoint,raw", [("chat_completions", CHAT), ("responses", RESPONSES), ("messages", MESSAGES)]
)
def test_usage_handles_every_byte_boundary_without_rewriting(endpoint, raw):
    observer = UsageObserver(endpoint, True)
    assert b"".join(observer.feed(bytes([byte])) for byte in raw) == raw
    value = observer.finish()
    assert (value.input_tokens, value.output_tokens, value.cached_input_tokens) == (5, 3, 2)


@pytest.mark.parametrize(
    "raw",
    [
        b"data: [DONE]\n\n",
        CHAT[:-2],
        CHAT + CHAT,
        sse({"usage": {"prompt_tokens": True, "completion_tokens": 1}}),
    ],
)
def test_invalid_or_missing_usage_is_not_success(raw):
    o = UsageObserver("chat_completions", True)
    with pytest.raises(BackendError):
        o.feed(raw)
        o.finish()


class Stream:
    status_code = 200
    headers = (("content-type", "text/event-stream"), ("set-cookie", "secret"))
    job_id = "j"

    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = self.completed = self.disconnected_count = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def complete(self):
        self.completed += 1

    async def disconnected(self):
        self.disconnected_count += 1

    async def aclose(self):
        self.closed += 1


class Backend:
    async def report_usage(self, job_id, usage):
        pass

    def __init__(self, stream):
        self.stream = stream
        self.calls = []

    async def open(self, req):
        self.calls.append(req)
        return self.stream


async def test_forwarding_accounts_once_and_closes_without_rewriting():
    stream = Stream([CHAT[:15], CHAT[15:]])
    backend = Backend(stream)
    req = request(b'{"model":"m","stream":true,"stream_options":{"include_usage":true}}')
    jobs = []
    measured = []
    outcomes = []
    async with forward(
        backend,
        req,
        bind_job=lambda *v: jobs.append(v),
        record_usage=lambda *v, measured=measured: measured.append(v),
        record_outcome=lambda *v, outcomes=outcomes: outcomes.append(v),
    ) as delivery:
        assert delivery.headers == (("content-type", "text/event-stream"),)
        assert b"".join([part async for part in delivery.body]) == CHAT
    assert backend.calls == [req] and jobs == [("r", "j")]
    assert len(measured) == 1 and outcomes == [("r", "succeeded")]
    assert (stream.completed, stream.closed, stream.disconnected_count) == (1, 1, 0)


async def test_close_before_body_and_missing_usage_keep_failure():
    for consume in [False, True]:
        stream = Stream([b"data: [DONE]\n\n"])
        outcomes = []
        measured = []
        try:
            async with forward(
                Backend(stream),
                request(),
                bind_job=lambda *v: None,
                record_usage=lambda *v, measured=measured: measured.append(v),
                record_outcome=lambda *v, outcomes=outcomes: outcomes.append(v),
            ) as delivery:
                if consume:
                    _ = [part async for part in delivery.body]
        except BackendError:
            pass
        assert stream.closed == 1 and stream.completed == 0 and stream.disconnected_count == 1
        assert not measured and outcomes == [("r", "failed")]


async def test_client_cancellation_closes_and_records_disconnect():
    import asyncio

    entered = asyncio.Event()
    stream = Stream([])
    outcomes = []

    async def serve():
        async with forward(
            Backend(stream),
            request(),
            bind_job=lambda *v: None,
            record_usage=lambda *v: None,
            record_outcome=lambda *v, outcomes=outcomes: outcomes.append(v),
        ):
            entered.set()
            await asyncio.Future()

    task = asyncio.create_task(serve())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert outcomes == [("r", "disconnected")]
    assert stream.closed == stream.disconnected_count == 1


async def test_deadline_covers_downstream_wait_and_closes():
    import asyncio

    stream = Stream([])
    outcomes = []
    with pytest.raises(TimeoutError):
        async with forward(
            Backend(stream),
            request(),
            bind_job=lambda *v: None,
            record_usage=lambda *v: None,
            record_outcome=lambda *v, outcomes=outcomes: outcomes.append(v),
            deadline_seconds=0.01,
        ):
            await asyncio.Future()
    assert outcomes == [("r", "failed")]
    assert stream.closed == stream.disconnected_count == 1


async def test_provider_error_status_and_body_are_preserved():
    raw = b'{ "error": {"code":"future_error", "detail":"runtime"} }'
    stream = Stream([raw])
    stream.status_code = 429
    measured = []
    outcomes = []
    async with forward(
        Backend(stream),
        request(),
        bind_job=lambda *v: None,
        record_usage=lambda *v, measured=measured: measured.append(v),
        record_outcome=lambda *v, outcomes=outcomes: outcomes.append(v),
    ) as delivery:
        assert delivery.status_code == 429
        assert b"".join([p async for p in delivery.body]) == raw
    assert not measured and outcomes == [("r", "failed")]
    assert stream.completed == 0 and stream.closed == 1


async def test_callback_failure_still_releases_opened_stream():
    stream = Stream([])

    def broken(*args):
        raise RuntimeError("ledger unavailable")

    with pytest.raises(RuntimeError):
        async with forward(
            Backend(stream),
            request(),
            bind_job=broken,
            record_usage=lambda *v: None,
            record_outcome=lambda *v: None,
        ):
            pytest.fail("binding failed before delivery")
    assert stream.closed == stream.disconnected_count == 1


def test_usage_observation_limits_and_failed_terminal_preserve_measurement():
    o = UsageObserver("responses", True)
    o.feed(
        sse(
            {
                "type": "response.incomplete",
                "response": {"usage": {"input_tokens": 1, "output_tokens": 2}},
            }
        )
    )
    with pytest.raises(BackendError):
        o.finish()
    assert o.measured.input_tokens == 1
    for streaming in [True, False]:
        observer = UsageObserver("chat_completions", streaming, limit=4)
        with pytest.raises(BackendError):
            observer.feed(b"12345")


@pytest.mark.parametrize(
    "endpoint,raw",
    [
        ("chat_completions", {"usage": {"prompt_tokens": 5, "completion_tokens": 3}}),
        ("responses", {"status": "completed", "usage": {"input_tokens": 5, "output_tokens": 3}}),
        ("messages", {"usage": {"input_tokens": 5, "output_tokens": 3}}),
    ],
)
def test_unary_usage_keeps_payload_bytes(endpoint, raw):
    body = json.dumps(raw).encode()
    observer = UsageObserver(endpoint, False)
    assert observer.feed(body) is body
    assert observer.finish().output_tokens == 3


async def test_native_selection_embeds_in_real_engine_before_transport():
    from livepeer_builder import ActorContext, EngineSupply
    from livepeer_builder.testing import ScriptedStreamTransport, StreamScript, make_engine

    right, approval = supply()
    wrong = right.model_copy(
        update={"url": "https://wrong", "runner_id": "wrong", "capacity_available": 100}
    )
    transport = ScriptedStreamTransport(opens=[StreamScript(events=[CHAT])])
    policy = NativeSelection(MostCapacityFirst(), lambda: (approval,), now=lambda: 100)
    async with make_engine(
        runners=[wrong, right],
        supply=EngineSupply(transport=transport, selection=policy),
        allocation_mode="per_application",
        shared_payment_key="fixture",
        shared_allocation_id="fixture",
    ) as engine:
        actor = ActorContext(
            actor_id="a", application_id="app", permissions=frozenset({"jobs.run", "jobs.read"})
        )
        stream = await engine.jobs.streaming.stream(
            actor, JobRequest(app="app", model="m", path="/v1/chat/completions")
        )
        async with stream:
            async for _ in stream:
                pass
        assert len(transport.calls) == 1 and transport.calls[0][0] == right.url
