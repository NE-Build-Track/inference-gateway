import pytest
from livepeer_builder import ActorContext, EngineSupply, JobRequest
from livepeer_builder.services.discovery import MostCapacityFirst
from livepeer_builder.testing import (
    FakeEngine,
    ScriptedStreamTransport,
    StreamScript,
    make_engine,
    runner,
)

from inference_gateway.backends.discovery import ModelSelection, model_name
from inference_gateway.backends.livepeer import LivepeerBackend
from inference_gateway.config import LivepeerSettings, ModelRoute
from inference_gateway.core.backend import BackendUnavailable
from inference_gateway.core.models import Generate, Message, Text


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        "bad",
        "[]",
        "{}",
        '{"model_name":null}',
        '{"model_name":42}',
        '{"model_name":" "}',
        '{"model_name":" gemma"}',
    ],
)
def test_invalid_model_metadata_is_not_an_identity(metadata):
    assert model_name(runner("gemma", metadata=metadata)) is None


def test_policy_filters_model_app_and_capacity_before_ranking():
    policy = ModelSelection(MostCapacityFirst())
    wrong = runner(
        "chat", url="https://wrong", metadata='{"model_name":"qwen"}', capacity_available=100
    )
    right = runner(
        "chat", url="https://right", metadata='{"model_name":"gemma"}', capacity_available=1
    )
    busy = right.model_copy(update={"url": "https://busy", "capacity_available": 0})
    other = right.model_copy(update={"app": "other", "url": "https://other"})
    assert policy.order([wrong, busy, other, right], JobRequest(app="chat", model="gemma")) == [
        right
    ]
    from livepeer_builder import NoCapacity

    with pytest.raises(NoCapacity):
        policy.order([wrong], JobRequest(app="chat", model="gemma"))


async def test_catalog_deduplicates_models_and_excludes_invalid_busy_and_unapproved():
    candidates = [
        runner("a", metadata='{"model_name":"gemma"}', orchestrator_url="https://allowed"),
        runner("b", metadata='{"model_name":"gemma"}', orchestrator_url="https://allowed"),
        runner("a", metadata='{"model_name":"qwen"}', orchestrator_url="https://other"),
        runner("a", metadata="{}", orchestrator_url="https://allowed"),
    ]
    engine = FakeEngine(streaming=True, runners=candidates)
    options = LivepeerSettings(
        routes=(
            ModelRoute(name="gemma", app="a"),
            ModelRoute(name="gemma", app="b"),
            ModelRoute(name="qwen", app="a"),
        ),
        approved_orchestrators=("https://allowed",),
    )
    backend = LivepeerBackend(engine, options)
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
        runner(
            "chat", url="https://wrong", metadata='{"model_name":"qwen"}', capacity_available=100
        ),
        runner(
            "chat", url="https://right", metadata='{"model_name":"gemma"}', capacity_available=1
        ),
    ]
    async with make_engine(
        runners=candidates,
        supply=EngineSupply(transport=transport, selection=ModelSelection(MostCapacityFirst())),
        allocation_mode="per_application",
        shared_payment_key="fixture",
        shared_allocation_id="fixture",
    ) as engine:
        actor = ActorContext(
            actor_id="a", application_id="app", permissions=frozenset({"jobs.run", "jobs.read"})
        )
        stream = await engine.jobs.streaming.stream(
            actor, JobRequest(app="chat", model="gemma", path="/v1/chat/completions")
        )
        async with stream:
            async for _ in stream:
                pass
        assert len(transport.calls) == 1
        assert transport.calls[0][0] == "https://right"


async def test_duplicate_model_apps_select_capable_app_without_retrying():
    from livepeer_builder.testing import Reply

    from inference_gateway.core.models import Sampling
    from inference_gateway.core.service import GenerationService

    raw = (
        b'data: {"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}],'
        b'"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\ndata: [DONE]\n\n'
    )
    engine = FakeEngine(
        streaming=True,
        runners=[
            runner("a", metadata='{"model_name":"gemma"}', capacity_available=50),
            runner("b", metadata='{"model_name":"gemma"}', capacity_available=1),
        ],
        replies={
            ("b", "/v1/chat/completions"): Reply(content_type="text/event-stream", chunks=(raw,))
        },
    )
    options = LivepeerSettings(
        routes=(
            ModelRoute(name="gemma", app="a"),
            ModelRoute(name="gemma", app="b", capabilities={"sampling"}),
        )
    )
    result = await GenerationService(LivepeerBackend(engine, options)).complete(
        Generate("gemma", (Message("user", (Text("hi"),)),), sampling=Sampling(top_p=0.5))
    )
    assert result.content == (Text("ok"),)
    assert len(engine.jobs.requests) == 1 and engine.jobs.requests[0][1].app == "b"


async def test_freshness_is_required_for_catalog_and_dispatch():
    engine = FakeEngine(streaming=True, runners=[runner("a", metadata='{"model_name":"gemma"}')])
    health = engine.health()
    engine.health = lambda: health.model_copy(update={"discovery_fresh": False})
    backend = LivepeerBackend(engine, LivepeerSettings(routes=(ModelRoute(name="gemma", app="a"),)))
    assert backend.models() == () and not backend.ready()
    with pytest.raises(BackendUnavailable):
        await anext(backend.stream(Generate("gemma", (Message("user", (Text("hi"),)),))))
    assert not engine.jobs.requests


async def test_no_matching_candidate_never_opens_paid_transport():
    from livepeer_builder import NoCapacity

    transport = ScriptedStreamTransport(opens=[])
    async with make_engine(
        runners=[runner("chat", metadata='{"model_name":"qwen"}')],
        supply=EngineSupply(transport=transport, selection=ModelSelection(MostCapacityFirst())),
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
