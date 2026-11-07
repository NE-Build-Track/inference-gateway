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
from inference_gateway.backends.discovery import NATIVE, ContractSelection, FilteredDiscovery
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


def eligible(candidates, *, model="gemma", endpoint="chat_completions", approvals=(), only=False):
    return contract().eligible(
        candidates,
        capability="chat",
        model=model,
        endpoint=endpoint,
        approvals=FixedApprovals(approvals).approver(model),
        approved_only=only,
    )


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


async def test_discovery_fills_the_runner_id_from_the_envelope_and_refuses_conflicts():
    named = runner("chat", url="https://named", metadata=envelope("gemma", CHAT, runner_id="one"))
    conflict = runner("chat", url="https://two", metadata=envelope("gemma", CHAT, runner_id="one"))

    class Source:
        async def fetch(self):
            return (named.model_copy(update={"runner_id": None}), conflict)

    found = await FilteredDiscovery(Source(), frozenset({"chat"}), ()).fetch()
    assert [r.runner_id for r in found] == ["one"]


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
    meta = envelope("gemma", CHAT, admission=admission)
    held = runner("chat", url="https://runner", metadata=meta, orchestrator_url="https://orch")
    assert eligible([held]) == []
    assert eligible([held], approvals=(approval("https://orch", "runner", "gemma"),)) == [held]


def test_native_jobs_need_an_approval_for_every_runner():
    open_runner = runner("chat", url="https://runner", metadata=envelope("gemma", CHAT))
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
    candidate = runner("chat", url="https://runner", metadata=envelope("gemma", CHAT))
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
