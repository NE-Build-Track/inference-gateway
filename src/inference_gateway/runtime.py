"""Application composition: configuration loading and ownership of the embedded engine."""

import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Literal, cast

import anyio
from livepeer_builder import ActorApi, BuilderEngine, EngineParts, EngineSettings, JobRequest
from livepeer_builder.api import JobStreamApi

from inference_gateway.backends.approvals import ApprovalFile, Approvals, FixedApprovals
from inference_gateway.backends.discovery import ContractSelection, FilteredDiscovery, origin
from inference_gateway.backends.livepeer import LivepeerBackend
from inference_gateway.backends.native_livepeer import LivepeerNativeBackend
from inference_gateway.backends.runner_contract import ENDPOINTS, RunnerContract
from inference_gateway.config import LivepeerSettings, ModelRoute, Settings
from inference_gateway.core.models import Finished


@dataclass(frozen=True)
class RunnerPolicy:
    """The pinned registry and the operator's approvals, shared by selection and the backends."""

    contract: RunnerContract
    approvals: Approvals


EngineFactory = Callable[[LivepeerSettings, RunnerPolicy], Awaitable[ActorApi]]


def load_policy(settings: LivepeerSettings, contract: RunnerContract | None = None) -> RunnerPolicy:
    """Checks routes against the registry and reads the approvals file; errors name no values."""
    contract = contract or RunnerContract.load()
    for route in settings.routes:
        if not contract.is_capability(route.capability):
            raise ValueError("Route capability is not on the standard's capability list")
        endpoints = (route.upstream_api,) if route.mode == "validated" else tuple(ENDPOINTS)
        if not any(contract.serves(route.capability, endpoint) for endpoint in endpoints):
            raise ValueError("No gateway endpoint serves the route's capability")
    approvals: Approvals = (
        ApprovalFile(settings.approvals_file, contract)
        if settings.approvals_file
        else FixedApprovals()
    )
    return RunnerPolicy(contract, approvals)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    values = os.environ if env is None else env
    backend = values.get("GATEWAY_BACKEND", "fake")
    return Settings(
        api_key=values.get("GATEWAY_API_KEY"),
        accounting_db=values.get("GATEWAY_ACCOUNTING_DB") or None,
        timeout_seconds=float(values.get("GATEWAY_TIMEOUT_SECONDS", "300")),
        backend=cast(Literal["fake", "livepeer"], backend),
    )


def load_livepeer_settings(
    config: Settings, env: Mapping[str, str] | None = None
) -> LivepeerSettings:
    values = os.environ if env is None else env
    try:
        raw_routes = values.get("GATEWAY_MODEL_ROUTES")
        routes = (
            tuple(ModelRoute.model_validate(r) for r in json.loads(raw_routes))
            if raw_routes
            else (ModelRoute(name=values.get("GATEWAY_MODEL_NAME", "qwen3.8-27b")),)
        )
        options: dict[str, object] = {
            "routes": routes,
            "approvals_file": values.get("GATEWAY_APPROVALS_FILE") or None,
            "timeout_seconds": config.timeout_seconds,
            "approved_orchestrators": tuple(
                x.strip()
                for x in values.get("GATEWAY_APPROVED_ORCHESTRATORS", "").split(",")
                if x.strip()
            ),
        }
        for field in [
            "application_id",
            "actor_id",
            "first_byte_timeout_seconds",
            "idle_timeout_seconds",
            "max_attempts",
            "max_event_bytes",
            "max_output_bytes",
        ]:
            if (raw := values.get("GATEWAY_" + field.upper())) is not None:
                options[field] = raw
        settings = LivepeerSettings.model_validate(options)
        for url in settings.approved_orchestrators:
            origin(url)
        return settings
    except (ValueError, TypeError):
        raise ValueError(
            "Invalid gateway Livepeer configuration; check model routes and limits"
        ) from None


async def build_engine(settings: LivepeerSettings, policy: RunnerPolicy) -> ActorApi:
    if any(route.mode == "native" for route in settings.routes):
        required_fields = {"raw_body", "content_type", "headers", "return_http_errors"}
        if not required_fields <= JobRequest.model_fields.keys() or not hasattr(
            JobStreamApi, "headers"
        ):
            raise ValueError(
                "Installed Builder Core lacks the native HTTP contract; "
                "restore this gateway's locked dependencies with uv sync --locked"
            )
    try:
        engine_settings = EngineSettings.from_env()
        if (
            ":memory:" in engine_settings.database_url
            or "mode=memory" in engine_settings.database_url
        ):
            raise ValueError("Livepeer requires a durable engine database")
        if engine_settings.allocation_mode != "per_application":
            raise ValueError("Shared application allocation required")
        required = (
            engine_settings.signer_url,
            engine_settings.discovery_url,
            engine_settings.batteries_url,
            engine_settings.batteries_token,
            engine_settings.shared_payment_key,
            engine_settings.shared_allocation_id,
            engine_settings.secret_key,
        )
        if not all(required):
            raise ValueError("Missing payment or discovery configuration")
    except ValueError:
        # Pydantic error strings can contain input values; never expose configuration secrets.
        raise ValueError(
            "Livepeer requires LPB_DISCOVERY_URL, valid LPB_* payment settings, "
            "and a shared application allocation"
        ) from None

    def decorate(parts: EngineParts) -> EngineParts:
        return replace(
            parts,
            selection=ContractSelection(parts.selection, policy.contract, policy.approvals),
            discovery_source=FilteredDiscovery(
                parts.discovery_source,
                frozenset(route.capability for route in settings.routes),
                settings.approved_orchestrators,
            ),
        )

    return await BuilderEngine.create(engine_settings, decorate=decorate)


@asynccontextmanager
async def livepeer_backend(
    settings: LivepeerSettings,
    factory: EngineFactory,
    usage_recorder: Callable[[str, Finished], None] | None = None,
    job_recorder: Callable[[str, str], None] | None = None,
    policy: RunnerPolicy | None = None,
) -> AsyncIterator[LivepeerBackend]:
    policy = policy or load_policy(settings)
    engine = await factory(settings, policy)
    try:
        native = LivepeerNativeBackend(engine, settings, policy.contract, policy.approvals)
        backend = LivepeerBackend(
            engine,
            settings,
            policy.contract,
            policy.approvals,
            usage_recorder,
            job_recorder,
            native_backend=native,
        )
        await engine.start()
        yield backend
    finally:
        with anyio.CancelScope(shield=True):
            await engine.stop()
