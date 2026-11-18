"""Runner contract v1 fixtures: envelopes, conformance reports and approvals for tests."""

import functools
import json

from inference_gateway.backends.approvals import Approval, FixedApprovals
from inference_gateway.backends.livepeer import LivepeerBackend
from inference_gateway.backends.runner_contract import ENDPOINTS, RunnerContract
from inference_gateway.runtime import RunnerPolicy

REVISION = "sha256:" + "a" * 64
FAR_FUTURE = 4102444800.0  # 2100-01-01
# The chat profiles, as a chat runner serving every validated and native chat surface declares.
CHAT = ("chat_completions", "responses", "messages")


@functools.cache
def contract() -> RunnerContract:
    return RunnerContract.load()


def envelope(
    model: str = "m",
    endpoints: tuple[str, ...] = ("chat_completions",),
    *,
    revision: str = REVISION,
    runner_id: str | None = None,
    admission: str | None = None,
    source: str | None = None,
    **extra: object,
) -> str:
    """A runner's `Metadata`: a compact `runner_contract: 1` envelope."""
    value: dict[str, object] = {
        "runner_contract": 1,
        "deployment_revision": revision,
        "model": {"name": model, "source": source or f"hf:test/{model}"},
        "endpoints": {name: {"profile": ENDPOINTS[name]} for name in endpoints},
    }
    if runner_id is not None:
        value["runner_id"] = runner_id
    if admission is not None:
        value["admission"] = admission
    value.update(extra)
    return json.dumps(value, separators=(",", ":"))


def report(
    runner_id: str, revision: str = REVISION, profile: str = "openai.chat.v2", *, passed=True
) -> dict:
    """The smallest conformance report its schema accepts."""
    return {
        "conformance_report": 1,
        "runner_id": runner_id,
        "deployment_revision": revision,
        "profile": profile,
        "registry_release": "2026.11.3",
        "suite": {"id": profile, "version": "1+common.1"},
        "tool": {"name": "runner-contract", "version": "0.2.0"},
        "started_at": "2026-11-05T12:00:00Z",
        "finished_at": "2026-11-05T12:01:00Z",
        "checks": [{"id": "common.envelope", "result": "pass" if passed else "fail"}],
        "pass": passed,
    }


def approval(
    orchestrator: str = "https://orch",
    runner_id: str = "runner",
    model: str = "m",
    endpoint: str = "chat_completions",
    *,
    revision: str = REVISION,
    expires_at: float = FAR_FUTURE,
) -> Approval:
    profile = ENDPOINTS[endpoint]
    return Approval(
        orchestrator=orchestrator,
        runner_id=runner_id,
        model=model,
        deployment_revision=revision,
        profile=profile,
        expires_at=expires_at,
        conformance=report(runner_id, revision, profile),
    )


def policy(*approvals: Approval, now=None) -> RunnerPolicy:
    fixed = FixedApprovals(approvals) if now is None else FixedApprovals(approvals, now=now)
    return RunnerPolicy(contract(), fixed)


def validated_backend(engine, settings, *args, approvals=(), **kwargs) -> LivepeerBackend:
    """A validated backend on the pinned registry, with fixed approvals."""
    return LivepeerBackend(engine, settings, contract(), FixedApprovals(approvals), *args, **kwargs)
