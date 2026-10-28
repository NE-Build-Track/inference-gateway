import json

import httpx
import pytest
from livepeer_builder.testing import FakeEngine, runner

from inference_gateway.app import Settings, create_app
from inference_gateway.backends.discovery import FilteredDiscovery
from inference_gateway.config import LivepeerSettings, ModelRoute
from inference_gateway.runtime import build_engine, load_livepeer_settings, load_settings


async def test_lifespan_owns_engine_and_readiness():
    app_name = "chat/test"
    engine = FakeEngine(
        streaming=True, runners=[runner(app_name, metadata='{"model_name":"public"}')]
    )
    options = LivepeerSettings(routes=(ModelRoute(name="public", app=app_name),))

    async def factory(settings):
        assert settings == options
        return engine

    app = create_app(
        settings=Settings(backend="livepeer"), livepeer_settings=options, engine_factory=factory
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        assert (await c.get("/readyz")).status_code == 503
        assert (await c.get("/healthz")).status_code == 200
        async with app.router.lifespan_context(app):
            assert engine.started and not engine.stopped
            assert (await c.get("/readyz")).status_code == 200
        assert engine.stopped
        assert (await c.get("/readyz")).status_code == 503


@pytest.mark.parametrize("fail_start", [False, True])
async def test_startup_failure_closes_engine(fail_start):
    engine = FakeEngine(streaming=fail_start)
    if fail_start:

        async def start():
            raise RuntimeError("fixture startup failure")

        engine.start = start

    async def factory(settings):
        return engine

    app = create_app(
        settings=Settings(backend="livepeer"),
        livepeer_settings=LivepeerSettings(),
        engine_factory=factory,
    )
    with pytest.raises((ValueError, RuntimeError)):
        async with app.router.lifespan_context(app):
            pytest.fail("Startup should fail")
    assert engine.stopped


async def test_empty_discovery_is_not_ready():
    engine = FakeEngine(streaming=True)

    async def factory(settings):
        return engine

    app = create_app(
        settings=Settings(backend="livepeer"),
        livepeer_settings=LivepeerSettings(),
        engine_factory=factory,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            assert (await c.get("/readyz")).status_code == 503


def test_routes_and_limits_from_environment():
    env = {
        "GATEWAY_BACKEND": "livepeer",
        "GATEWAY_TIMEOUT_SECONDS": "45",
        "GATEWAY_MODEL_ROUTES": json.dumps(
            [
                {"name": "one", "app": "a", "upstream_model": "one"},
                {"name": "two", "app": "b", "disable_thinking": False},
            ]
        ),
        "GATEWAY_ACTOR_ID": "actor",
        "GATEWAY_FIRST_BYTE_TIMEOUT_SECONDS": "10",
        "GATEWAY_APPROVED_ORCHESTRATORS": "https://orch.example:443,http://local:8937",
    }
    settings = load_settings(env)
    config = load_livepeer_settings(settings, env)
    assert config.actor_id == "actor" and config.timeout_seconds == 45
    assert config.first_byte_timeout_seconds == 10
    assert config.routes[0].upstream_model == "one"
    assert not config.routes[1].disable_thinking


@pytest.mark.parametrize(
    "values",
    [
        {"GATEWAY_MODEL_ROUTES": "{}"},
        {"GATEWAY_MODEL_ROUTES": '[{"name":"a","app":"b"},{"name":"a","app":"b"}]'},
        {"GATEWAY_MODEL_ROUTES": '[{"name":"a","app":"b","typo":true}]'},
        {"GATEWAY_MAX_ATTEMPTS": "0"},
        {"GATEWAY_APPROVED_ORCHESTRATORS": "https://user:private-secret@host"},
        {"GATEWAY_IDLE_TIMEOUT_SECONDS": "nan"},
    ],
)
def test_bad_livepeer_configuration_is_sanitized(values):
    with pytest.raises(ValueError) as exc:
        load_livepeer_settings(Settings(), values)
    assert "private-secret" not in str(exc.value)


async def test_payment_configuration_fails_without_values_or_secrets_in_error(monkeypatch):
    # Explicitly clear potential developer configuration without reading it into output.
    monkeypatch.setenv("LPB_ALLOCATION_MODE", "per_actor")
    monkeypatch.setenv("LPB_SHARED_PAYMENT_KEY", "private-secret")
    with pytest.raises(ValueError) as exc:
        await build_engine(LivepeerSettings())
    assert "private-secret" not in str(exc.value)


async def test_discovery_filters_app_and_origin_without_owning_source():
    class Source:
        async def fetch(self):
            return (
                runner("yes", orchestrator_url="https://orch.example"),
                runner("yes", orchestrator_url="https://orch.example:444"),
                runner("no", orchestrator_url="https://orch.example"),
                runner("yes", orchestrator_url="https://other.example"),
            )

    source = FilteredDiscovery(Source(), frozenset({"yes"}), ("https://orch.example:443",))
    found = await source.fetch()
    assert len(found) == 1 and found[0].orchestrator_url == "https://orch.example"


@pytest.mark.parametrize("mode", ["validated", "native"])
async def test_discovery_configuration_is_required_before_engine_creation(
    monkeypatch, tmp_path, mode
):
    from cryptography.fernet import Fernet

    from inference_gateway.runtime import BuilderEngine

    for name, value in {
        "LPB_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path}/engine.db",
        "LPB_ALLOCATION_MODE": "per_application",
        "LPB_SIGNER_URL": "http://signer.test:8937",
        "LPB_BATTERIES_URL": "http://batteries.test:8081",
        "LPB_BATTERIES_TOKEN": "fixture-token",
        "LPB_SHARED_PAYMENT_KEY": "fixture-payment-key",
        "LPB_SHARED_ALLOCATION_ID": "fixture-allocation",
        "LPB_SECRET_KEY": Fernet.generate_key().decode(),
    }.items():
        monkeypatch.setenv(name, value)
    options = LivepeerSettings(routes=(ModelRoute(name="m", app="chat", mode=mode),))
    monkeypatch.delenv("LPB_DISCOVERY_URL", raising=False)
    created = []
    engine = FakeEngine(streaming=True)

    async def create(settings, **kwargs):
        created.append(settings.discovery_url)
        return engine

    monkeypatch.setattr(BuilderEngine, "create", create)
    with pytest.raises(ValueError) as exc:
        await build_engine(options)
    assert "LPB_DISCOVERY_URL" in str(exc.value)
    assert "fixture-payment-key" not in str(exc.value)
    assert created == []
    monkeypatch.setenv("LPB_DISCOVERY_URL", "http://discovery.test:8939/discovery")
    assert await build_engine(options) is engine
    assert created == ["http://discovery.test:8939/discovery"]


@pytest.mark.parametrize("missing", ["raw_body", "content_type", "headers", "return_http_errors"])
async def test_native_contract_mismatch_fails_before_engine_creation(monkeypatch, missing):
    from inference_gateway.runtime import BuilderEngine, JobRequest

    fields = dict(JobRequest.model_fields)
    fields.pop(missing)
    monkeypatch.setattr(JobRequest, "__pydantic_fields__", fields)

    async def unexpected_create(*args, **kwargs):
        pytest.fail("Incompatible native engine must not open resources")

    monkeypatch.setattr(BuilderEngine, "create", unexpected_create)
    settings = LivepeerSettings(routes=(ModelRoute(name="m", app="chat", mode="native"),))
    with pytest.raises(ValueError, match="lacks the native HTTP contract"):
        await build_engine(settings)


async def test_native_requires_response_headers_before_engine_creation(monkeypatch):
    import inference_gateway.runtime as runtime

    monkeypatch.setattr(runtime, "JobStreamApi", object)
    settings = LivepeerSettings(routes=(ModelRoute(name="m", app="chat", mode="native"),))
    with pytest.raises(ValueError, match="uv sync --locked"):
        await build_engine(settings)
