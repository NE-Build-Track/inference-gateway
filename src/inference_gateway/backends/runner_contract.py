"""Runner contract v1, as this gateway reads it: envelopes from discovery and the pinned registry.

The standard's library does the reading and the selection rules. This module adds only what the
standard leaves to each gateway: which profiles its surfaces speak, capacity, and approval policy.
"""

import functools
from collections.abc import Callable, Iterable
from typing import Any

from livepeer_builder import Runner
from livepeer_runner_contract import (
    MAX_METADATA_BYTES,
    ApprovalKey,
    Envelope,
    Ineligible,
    Registry,
    ResultReader,
    parse_envelope,
    schema_problems,
    select,
    validate_request,
)
from livepeer_runner_contract import Runner as ContractRunner
from livepeer_runner_contract.standard import conformance_report_validator

# Each surface's wire profile. Translation code is written per protocol, so the pairing is fixed
# here; paths, framing, success, errors and request schemas come from the profile's entry.
ENDPOINTS = {
    "chat_completions": "openai.chat.v2",
    "responses": "openai.responses.stateless.v2",
    "messages": "anthropic.messages.v2",
    "systemone": "typesafe.systemone.v1",
}

Approver = Callable[[ApprovalKey], bool]


@functools.lru_cache(maxsize=4096)
def _parse(raw: str) -> Envelope | Ineligible:
    return parse_envelope(raw)


def read_envelope(raw: str | None) -> Envelope | Ineligible:
    """A runner's envelope, or why it has none. Never raises; oversized records aren't cached."""
    if raw is None or len(raw) > MAX_METADATA_BYTES:
        return parse_envelope(raw)
    return _parse(raw)


def runner_identity(runner: Runner, envelope: Envelope) -> str | None:
    """The node's runner ID, else the envelope's; None when the two disagree.

    Identification, not authorization: an ID is never guessed from an App ID, URL or hostname. An
    empty ID can't match an approval, so such a runner serves only where none is required.
    """
    if runner.runner_id and envelope.runner_id and runner.runner_id != envelope.runner_id:
        return None
    return runner.runner_id or envelope.runner_id or ""


class RunnerContract:
    def __init__(self, registry: Registry) -> None:
        for endpoint, profile in ENDPOINTS.items():
            registry.operation(profile, endpoint)  # a release without one of them can't serve
        self.registry = registry
        self._endpoints = {self.path(e): e for e in ENDPOINTS}

    @classmethod
    def load(cls) -> "RunnerContract":
        """The installed registry release, with every entry's digest checked."""
        return cls(Registry.load())

    @property
    def release(self) -> str | None:
        return self.registry.release

    def path(self, endpoint: str) -> str:
        return self.registry.operation(ENDPOINTS[endpoint], endpoint).path

    def endpoint_for(self, path: str) -> str | None:
        return self._endpoints.get(path)

    def is_capability(self, name: str) -> bool:
        return name in (self.registry.capabilities or {})

    def serves(self, capability: str, endpoint: str) -> bool:
        return capability in self.registry.entry(ENDPOINTS[endpoint]).capabilities

    def is_profile(self, profile: str) -> bool:
        return self.registry.canonical(profile) == profile

    def problems(self, endpoint: str, body: Any) -> tuple[str, ...]:
        """How a request body breaks the entry's request schema; empty when it matches.

        Checked before payment. Each line says where and what the schema wants, never the value,
        so it can go back to the caller. Types the entry doesn't describe, such as a coding agent's
        custom tools, pass and the runner decides. Pure CPU, tens of milliseconds for a large
        conversation, so async callers run it in a worker thread.
        """
        return validate_request(self.registry, ENDPOINTS[endpoint], endpoint, body).problems

    def reader(self, endpoint: str, *, status: int, content_type: str | None) -> ResultReader:
        """Reads an answer by the entry's error mappings and success rule as it streams."""
        operation = self.registry.operation(ENDPOINTS[endpoint], endpoint)
        return ResultReader(operation, status=status, content_type=content_type)

    def report_problems(self, report: object) -> list[str]:
        """A conformance report's schema problems, never quoting the report's own values."""
        errors = conformance_report_validator().iter_errors(report)
        return list(schema_problems(errors, "the report"))

    def eligible(
        self,
        candidates: Iterable[Runner],
        *,
        capability: str,
        model: str,
        endpoint: str,
        approvals: Approver,
        approved_only: bool,
    ) -> list[Runner]:
        """Runners serving `model` on `endpoint`, in their given order.

        A runner needs an approval where its endpoint is private, or for every endpoint when
        `approved_only` is set. Builder Core sends one path per job, so a runner whose envelope
        overrides the entry's path or method doesn't match. A runner reporting no free capacity
        doesn't match; one reporting none at all does only where approvals aren't required.
        """
        profile = ENDPOINTS[endpoint]
        operation = self.registry.operation(profile, endpoint)
        runners: dict[int, Runner] = {}
        contract_runners = []
        for runner in candidates:
            envelope = read_envelope(runner.metadata)
            available = runner.capacity_available
            if isinstance(envelope, Ineligible) or (available is not None and available <= 0):
                continue
            if approved_only and available is None:
                continue
            identity = runner_identity(runner, envelope)
            if identity is None:
                continue
            contract = ContractRunner(
                orchestrator=runner.orchestrator_url or "",
                runner_id=identity,
                app=runner.app,
                envelope=envelope,
                url=runner.url,
            )
            runners[id(contract)] = runner
            contract_runners.append(contract)
        selected: dict[int, Runner] = {}
        for choice in select(
            contract_runners,
            capability,
            model=model,
            profile=profile,
            approvals=approvals,
            registry=self.registry,
            approved_only=approved_only,
        ):
            if (
                choice.name == endpoint
                and choice.path == operation.path
                and choice.method == operation.method
            ):
                selected.setdefault(id(choice.runner), runners[id(choice.runner)])
        return list(selected.values())
