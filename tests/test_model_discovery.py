"""Runner contract v1 discovery and selection: what runners publish, and what the gateway admits."""

import json
import logging

import pytest
from contract_helpers import CHAT, approval, contract, envelope
from livepeer_builder import ActorContext, EngineSupply, JobRequest, NoCapacity
from livepeer_builder.services.discovery import MostCapacityFirst
from livepeer_builder.testing import (
    FakeEngine,
    ScriptedStreamTransport,
    StreamScript,
    make_engine,
    runner,
)
from livepeer_runner_contract import forget_ignored

from inference_gateway.backends.approvals import FixedApprovals
from inference_gateway.backends.discovery import (
    EARLIER,
    NATIVE,
    ContractSelection,
    FilteredDiscovery,
)
from inference_gateway.config import LivepeerSettings, ModelRoute
from inference_gateway.core.backend import BackendUnavailable
from inference_gateway.core.models import Generate, Message, Text

CHAT_PATH = "/v1/chat/completions"
INVALID = [
    None,
    "bad",
    "[]",
    "{}",
    # Before v1: ignored, never read for a model name.
    '{"model_name":"gemma"}',
    json.dumps({"inference_contract": 1, "model_name": "gemma", "endpoints": {}}),
    envelope("gemma", CHAT).replace('"runner_contract":1', '"runner_contract":2'),
    envelope("gemma", CHAT, attributes={"pad": "x" * 1024}),
    "[" * 5000 + "]" * 5000,
]


def selection(*approvals, inner=None):
    return ContractSelection(inner or MostCapacityFirst(), contract(), FixedApprovals(approvals))


def eligible(
    candidates,
    *,
    model="gemma",
    endpoint="chat_completions",
    approvals=(),
    only=False,
    earlier=False,
):
    return contract().eligible(
        candidates,
        capability="chat",
        model=model,
        endpoint=endpoint,
        approvals=FixedApprovals(approvals).approver(model),
        approved_only=only,
        earlier=earlier,
    )


def v1_envelope(model="gemma", **fields):
    """A runner that declares the earlier chat profiles, as the network's chat runner did in
    November 2026."""
    return envelope(model, CHAT, **fields).replace(".v2", ".v1")


@pytest.mark.parametrize("metadata", INVALID)
def test_a_record_that_is_not_a_v1_envelope_makes_only_its_runner_ineligible(metadata):
    good = runner("chat", url="https://good", metadata=envelope("gemma", CHAT))
    bad = runner("chat", url="https://bad", metadata=metadata)
    assert eligible([bad, good]) == [good]


@pytest.mark.parametrize("metadata", INVALID)
async def test_discovery_logs_each_ignored_runner_once_and_keeps_refreshing(metadata, caplog):
    forget_ignored()
    good = runner("chat", url="https://good", metadata=envelope("gemma", CHAT))
    bad = runner("chat", url="https://bad", metadata=metadata)

    class Source:
        async def fetch(self):
            return (bad, good)

    source = FilteredDiscovery(Source(), frozenset({"chat"}), ())
    with caplog.at_level(logging.INFO, "inference_gateway"):
        assert await source.fetch() == (good,)
        assert await source.fetch() == (good,)
    lines = [r.getMessage() for r in caplog.records if "ignoring runner" in r.getMessage()]
    assert len(lines) == 1
    assert "github.com/NE-Build-Track/live-runner-standard" in lines[0]
    assert "gemma" not in lines[0]  # reasons quote the schema, never the runner's values


async def test_a_runner_s_id_is_the_one_its_envelope_declares():
    """Builder Core's runners carry no ID of their own (its D63): the envelope is the source."""
    named = runner("chat", url="https://named", metadata=envelope("gemma", CHAT, runner_id="one"))
    nameless = runner("chat", url="https://nameless", metadata=envelope("gemma", CHAT))

    class Source:
        async def fetch(self):
            return (named, nameless)

    # Discovery keeps both as they came; a runner with no ID serves where no approval is needed.
    assert await FilteredDiscovery(Source(), frozenset({"chat"}), ()).fetch() == (named, nameless)
    assert eligible([named, nameless]) == [named, nameless]
    granted = approval("https://orch.example", "one", "gemma")
    assert eligible([named, nameless], approvals=(granted,), only=True) == [named]


def test_selection_filters_model_capability_profile_and_capacity_before_ranking():
    right = runner("chat", url="https://right", metadata=envelope("gemma"), capacity_available=1)
    wrong = right.model_copy(
        update={"url": "https://wrong", "metadata": envelope("qwen"), "capacity_available": 100}
    )
    busy = right.model_copy(update={"url": "https://busy", "capacity_available": 0})
    other = right.model_copy(update={"app": "decision", "url": "https://other"})
    responses_only = right.model_copy(
        update={"url": "https://responses", "metadata": envelope("gemma", ("responses",))}
    )
    # A chat App ID can't serve System One's profile, which serves `decision`.
    decision_profile = right.model_copy(
        update={"url": "https://decision", "metadata": envelope("gemma", ("systemone",))}
    )
    request = JobRequest(app="chat", model="gemma", path=CHAT_PATH)
    candidates = [wrong, busy, other, responses_only, decision_profile, right]
    assert selection().order(candidates, request) == [right]
    with pytest.raises(NoCapacity):
        selection().order([wrong], request)


@pytest.mark.parametrize("admission", ["private", "gold"])
def test_private_or_unknown_admission_needs_an_approval_on_validated_routes(admission):
    meta = envelope("gemma", CHAT, admission=admission, runner_id="runner")
    held = runner("chat", url="https://runner", metadata=meta, orchestrator_url="https://orch")
    assert eligible([held]) == []
    assert eligible([held], approvals=(approval("https://orch", "runner", "gemma"),)) == [held]


def test_native_jobs_need_an_approval_for_every_runner():
    meta = envelope("gemma", CHAT, runner_id="runner")
    open_runner = runner("chat", url="https://runner", metadata=meta)
    request = JobRequest(app="chat", model="gemma", path=CHAT_PATH, capability=NATIVE)
    with pytest.raises(NoCapacity):
        selection().order([open_runner], request)
    granted = approval("https://orch.example", "runner", "gemma")
    assert selection(granted).order([open_runner], request) == [open_runner]


@pytest.mark.parametrize(
    "change",
    [
        {"runner_id": "someone-else"},
        {"model": "qwen"},
        {"revision": "sha256:" + "c" * 64},
        {"endpoint": "responses"},
        {"orchestrator": "https://orch.example:8443"},
        {"expires_at": 1.0},
    ],
)
def test_an_approval_binds_runner_model_revision_profile_origin_and_expiry(change):
    meta = envelope("gemma", CHAT, runner_id="runner")
    candidate = runner("chat", url="https://runner", metadata=meta)
    fields = {"orchestrator": "https://orch.example", "runner_id": "runner", "model": "gemma"}
    granted = approval(**{**fields, **change})
    assert eligible([candidate], approvals=(granted,), only=True) == []
    assert eligible([candidate], approvals=(approval(**fields),), only=True) == [candidate]


def test_a_runner_that_moves_the_entry_path_is_not_selected():
    meta = json.loads(envelope("gemma"))
    meta["endpoints"]["chat_completions"]["path"] = "/openai/v1/chat/completions"
    moved = runner("chat", metadata=json.dumps(meta, separators=(",", ":")))
    assert eligible([moved]) == []


async def test_catalog_deduplicates_models_and_excludes_invalid_busy_and_unapproved():
    from contract_helpers import validated_backend

    candidates = [
        runner("chat", metadata=envelope("gemma", CHAT), orchestrator_url="https://allowed"),
        runner("chat", metadata=envelope("gemma", CHAT), orchestrator_url="https://allowed"),
        runner("chat", metadata=envelope("qwen", CHAT), orchestrator_url="https://other"),
        runner("chat", metadata="{}", orchestrator_url="https://allowed"),
    ]
    engine = FakeEngine(streaming=True, runners=candidates)
    options = LivepeerSettings(
        routes=(ModelRoute(name="gemma"), ModelRoute(name="qwen")),
        approved_orchestrators=("https://allowed",),
    )
    backend = validated_backend(engine, options)
    assert backend.models() == ("gemma",)
    with pytest.raises(BackendUnavailable):
        await anext(backend.stream(Generate("qwen", (Message("user", (Text("hi"),)),))))
    assert not engine.jobs.requests


async def test_injected_selection_real_engine_never_pays_wrong_model(tmp_path):
    chunks = (
        b'data: {"choices":[{"index":0,"delta":{"content":"hi"},"finish_reason":"stop"}],'
        b'"usage":{"prompt_tokens":2,"completion_tokens":1}}\n\n',
        b"data: [DONE]\n\n",
    )
    transport = ScriptedStreamTransport(opens=[StreamScript(events=list(chunks))])
    candidates = [
        runner("chat", url="https://wrong", metadata=envelope("qwen"), capacity_available=100),
        runner("chat", url="https://right", metadata=envelope("gemma"), capacity_available=1),
    ]
    async with make_engine(
        runners=candidates,
        supply=EngineSupply(transport=transport, selection=selection()),
        allocation_mode="per_application",
        shared_payment_key="fixture",
        shared_allocation_id="fixture",
    ) as engine:
        actor = ActorContext(
            actor_id="a", application_id="app", permissions=frozenset({"jobs.run", "jobs.read"})
        )
        stream = await engine.jobs.streaming.stream(
            actor, JobRequest(app="chat", model="gemma", path=CHAT_PATH)
        )
        async with stream:
            async for _ in stream:
                pass
        assert len(transport.calls) == 1
        assert transport.calls[0][0] == "https://right"


async def test_freshness_is_required_for_catalog_and_dispatch():
    from contract_helpers import validated_backend

    engine = FakeEngine(streaming=True, runners=[runner("chat", metadata=envelope("gemma", CHAT))])
    health = engine.health()
    engine.health = lambda: health.model_copy(update={"discovery_fresh": False})
    backend = validated_backend(engine, LivepeerSettings(routes=(ModelRoute(name="gemma"),)))
    assert backend.models() == () and not backend.ready()
    with pytest.raises(BackendUnavailable):
        await anext(backend.stream(Generate("gemma", (Message("user", (Text("hi"),)),))))
    assert not engine.jobs.requests


async def test_no_matching_candidate_never_opens_paid_transport():
    transport = ScriptedStreamTransport(opens=[])
    async with make_engine(
        runners=[runner("chat", metadata=envelope("qwen", CHAT))],
        supply=EngineSupply(transport=transport, selection=selection()),
        allocation_mode="per_application",
        shared_payment_key="fixture",
        shared_allocation_id="fixture",
    ) as engine:
        actor = ActorContext(
            actor_id="a", application_id="app", permissions=frozenset({"jobs.run", "jobs.read"})
        )
        with pytest.raises(NoCapacity):
            await engine.jobs.streaming.stream(
                actor, JobRequest(app="chat", model="gemma", path="/v1/responses")
            )
        jobs = await engine.jobs.list_jobs(actor)
        assert len(jobs) == 1 and jobs[0].state == "failed"
        assert not transport.calls and not jobs[0].attempts
        assert jobs[0].failure.payment_sent is False


# -- a runner on the earlier chat profile ---------------------------------------------------------


def test_a_v1_chat_runner_is_eligible_only_where_the_caller_allows_the_earlier_profile():
    v1 = runner("chat", url="https://v1", metadata=v1_envelope())
    v2 = runner("chat", url="https://v2", metadata=envelope("gemma", CHAT))
    assert eligible([v1, v2]) == [v2]
    assert eligible([v1, v2], earlier=True) == [v1, v2]  # in the candidates' order
    assert eligible([v1], earlier=True) == [v1]
    # The two Responses entries read an answer differently, so the earlier one is never admitted.
    assert eligible([v1, v2], endpoint="responses", earlier=True) == [v2]
    # Capacity, model and the envelope's own validity still decide.
    busy = v1.model_copy(update={"url": "https://busy", "capacity_available": 0})
    other = runner("chat", url="https://other", metadata=v1_envelope("qwen"))
    assert eligible([busy, other], earlier=True) == []


def test_an_approval_for_the_current_profile_does_not_admit_a_v1_runner():
    """An approval names its profile and a native job needs one: v1 runners serve validated jobs."""
    v1 = runner("chat", url="https://runner", metadata=v1_envelope(runner_id="runner"))
    granted = approval("https://orch.example", "runner", "gemma")
    assert eligible([v1], approvals=(granted,), only=True, earlier=True) == []
    private = runner("chat", url="https://p", metadata=v1_envelope(admission="private"))
    assert eligible([private], earlier=True) == []


def test_selection_sends_a_job_to_a_v1_runner_only_when_the_job_is_marked_for_it():
    v1 = runner("chat", url="https://v1", metadata=v1_envelope())
    v2 = runner("chat", url="https://v2", metadata=envelope("gemma", CHAT))
    marked = JobRequest(app="chat", model="gemma", path=CHAT_PATH, capability=EARLIER)
    unmarked = JobRequest(app="chat", model="gemma", path=CHAT_PATH)
    assert {r.url for r in selection().order([v1, v2], marked)} == {"https://v1", "https://v2"}
    assert selection().order([v1, v2], unmarked) == [v2]
    with pytest.raises(NoCapacity):
        selection().order([v1], unmarked)
    with pytest.raises(NoCapacity):  # a native job is never one
        native = JobRequest(app="chat", model="gemma", path=CHAT_PATH, capability=NATIVE)
        selection().order([v1], native)


@pytest.mark.parametrize(
    ("body", "fits"),
    [
        ({"model": "m", "messages": [{"role": "user", "content": "hi"}]}, True),
        (
            {
                "model": "m",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [{"type": "function", "function": {"name": "weather"}}],
            },
            True,
        ),
        (
            {
                "model": "m",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [{"type": "custom", "custom": {"name": "apply_patch"}}],
            },
            False,
        ),
    ],
)
def test_the_earlier_chat_profile_refuses_what_only_the_current_one_passes(body, fits):
    assert contract().problems("chat_completions", body) == ()
    assert (contract().problems("chat_completions", body, earlier=True) == ()) is fits
    assert contract().has_earlier("chat_completions") and not contract().has_earlier("responses")
