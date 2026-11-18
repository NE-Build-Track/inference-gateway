import json
import time
from decimal import Decimal
from uuid import uuid4

from contract_helpers import CHAT, envelope, validated_backend
from livepeer_builder import ActorContext
from livepeer_builder.contracts import AttemptCost, Fees, JobCost
from livepeer_builder.testing import FakeEngine, Reply, runner

from inference_gateway.backends.livepeer_accounting import LivepeerAccounting
from inference_gateway.config import LivepeerSettings, ModelRoute
from inference_gateway.core.models import Finished, Generate, Message, Text
from inference_gateway.core.service import GenerationService


async def test_wholesale_adapter_preserves_pending_recovers_usage_and_classifies_cost():
    app = "chat"
    actor = ActorContext(
        actor_id="shared-application",
        application_id="endpoint-gateway",
        permissions=frozenset({"jobs.run", "jobs.read", "costs.read"}),
    )

    def frame(data):
        return ("data: " + json.dumps(data) + "\n\n").encode()

    engine = FakeEngine(
        streaming=True,
        runners=[runner(app, metadata=envelope("test", CHAT))],
        replies={
            (app, "/v1/chat/completions"): Reply(
                content_type="text/event-stream",
                chunks=(
                    frame(
                        {
                            "choices": [
                                {"index": 0, "delta": {"content": "hi"}, "finish_reason": "stop"}
                            ]
                        }
                    ),
                    frame(
                        {
                            "choices": [],
                            "usage": {
                                "prompt_tokens": 10,
                                "completion_tokens": 2,
                                "prompt_tokens_details": {"cached_tokens": 8},
                            },
                        }
                    ),
                    b"data: [DONE]\n\n",
                ),
            )
        },
    )
    options = LivepeerSettings(routes=(ModelRoute(name="test", capability=app),))
    recorded = []
    jobs = []
    backend = validated_backend(
        engine,
        options,
        lambda rid, usage: recorded.append((rid, usage)),
        lambda rid, jid: jobs.append(jid),
    )
    await GenerationService(backend).complete(
        Generate("test", (Message("user", (Text("hi"),)),), request_id="req_a")
    )
    assert recorded == [("req_a", Finished("stop", 10, 2, 8))]
    source = LivepeerAccounting(engine, actor)
    observed = await source.observe("req_a", None, None, time.time())
    assert observed.job_id == jobs[0] and observed.amount is None and observed.status == "pending"
    assert engine.jobs.usage_reports[0][1]["cached_prompt_tokens"] == 8
    job = (await engine.jobs.list_jobs(actor))[0]
    engine.costs.costs[job.id] = JobCost(
        job_id=job.id,
        status="observed",
        fees=Fees(fee_usd=Decimal("0.12"), row_count=1),
        attempts=(
            AttemptCost(
                attempt_number=1,
                cost_class="delivered",
                fees=Fees(fee_usd=Decimal("0.12"), row_count=1),
            ),
        ),
        first_observed_at=None,
        updated_at=None,
    )
    updated = await source.observe("req_a", str(job.id), None, time.time())
    assert updated.amount == "0.12" and updated.basis == "ticket_expected_value"
    assert updated.attempts[0]["classification"] == "delivered"
    assert len(engine.jobs.requests) == 1
    assert (await source.observe("missing", str(uuid4()), None, time.time())).status == "unknown"


async def test_usage_reporting_retries_do_not_repeat_generation():
    from livepeer_builder import EngineSupply, JobRequest
    from livepeer_builder.testing import ScriptedTransport, make_engine

    app = "chat"
    actor = ActorContext(
        actor_id="a",
        application_id="app",
        permissions=frozenset({"jobs.run", "jobs.read", "costs.read"}),
    )
    transport = ScriptedTransport(steps=[ScriptedTransport.ok()])
    async with make_engine(
        runners=[runner(app, metadata=envelope("test", CHAT))],
        supply=EngineSupply(transport=transport),
        allocation_mode="per_application",
        shared_payment_key="fixture-key",
        shared_allocation_id="fixture-allocation",
    ) as engine:
        job = (
            await engine.jobs.run(actor, JobRequest(app=app, path="/", operation_ref="req_x"))
        ).job
        source = LivepeerAccounting(engine, actor)
        result = await source.observe("req_x", str(job.id), Finished("stop", 10, 2, 5), time.time())
        assert result.usage_reported and result.usage.cached_input_tokens == 5
        again = await source.observe("req_x", str(job.id), Finished("stop", 10, 2, 5), time.time())
        assert again.usage == result.usage
        assert len(transport.calls) == 1
        assert len(await engine.jobs.list_jobs(actor)) == 1


async def test_a_repeat_after_a_refused_payment_is_the_job_accounting_finds():
    """A job the signer refused never reached a runner, so the engine dispatches a repeat of its
    request id as a new job (builder-core D57). Both carry the id; accounting reads the newer
    one."""
    from livepeer_builder import EngineSupply, JobFailed, JobRequest, RunnerCallError
    from livepeer_builder.testing import ScriptedTransport, make_engine

    app = "chat"
    actor = ActorContext(
        actor_id="a",
        application_id="app",
        permissions=frozenset({"jobs.run", "jobs.read", "costs.read"}),
    )
    refused = RunnerCallError(
        "payment",
        "signer refused",
        payment_sent=False,
        status_code=402,
        reason="allowance_exhausted",
    )
    transport = ScriptedTransport(steps=[refused, ScriptedTransport.ok()])
    async with make_engine(
        runners=[runner(app, metadata=envelope("test", CHAT))],
        supply=EngineSupply(transport=transport),
        allocation_mode="per_application",
        shared_payment_key="fixture-key",
        shared_allocation_id="fixture-allocation",
    ) as engine:
        request = JobRequest(app=app, path="/", operation_ref="req_r")
        try:
            await engine.jobs.run(actor, request)
        except JobFailed as exc:
            first = exc.job
        else:
            raise AssertionError("the refused payment should fail the first job")
        second = (await engine.jobs.run(actor, request)).job
        assert second.id != first.id
        # This engine runs on a test clock, so admission time comes from it as well.
        admitted = first.created_at.timestamp()
        observed = await LivepeerAccounting(engine, actor).observe("req_r", None, None, admitted)
        assert observed.job_id == str(second.id)


async def test_cached_usage_roundtrips_through_anthropic_stream_sdk():
    import httpx
    from anthropic import AsyncAnthropic

    from inference_gateway.app import create_app
    from inference_gateway.core.models import BlockEnd, BlockStart, Delta

    class MeasuredBackend:
        async def stream(self, request):
            yield BlockStart(0)
            yield Delta(0, "hello")
            yield BlockEnd(0)
            yield Finished("stop", 100, 5, 80)

    app = create_app(MeasuredBackend())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as http:
        client = AsyncAnthropic(base_url="http://test", api_key="local", http_client=http)
        async with client.messages.stream(
            model="test", max_tokens=10, messages=[{"role": "user", "content": "hi"}]
        ) as stream:
            final = await stream.get_final_message()
        assert final.usage.input_tokens == 20
        assert final.usage.cache_read_input_tokens == 80
        assert final.usage.output_tokens == 5
