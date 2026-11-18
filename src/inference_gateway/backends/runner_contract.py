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

# The version before, where a runner that still declares it may serve a validated job. Only where
# the two versions differ in what a request may hold and read an answer the same way: v1 of the chat
# entry refuses the tools and content parts it doesn't describe, which v2 passes on to the runner.
# The two Responses entries map errors and success differently, so there is no earlier one for it.
EARLIER = {"chat_completions": "openai.chat.v1"}

Approver = Callable[[ApprovalKey], bool]


@functools.lru_cache(maxsize=4096)
def _parse(raw: str) -> Envelope | Ineligible:
    return parse_envelope(raw)


def read_envelope(raw: str | None) -> Envelope | Ineligible:
    """A runner's envelope, or why it has none. Never raises; oversized records aren't cached."""
    if raw is None or len(raw) > MAX_METADATA_BYTES:
        return parse_envelope(raw)
    return _parse(raw)


def runner_identity(envelope: Envelope) -> str:
    """The runner ID the envelope declares, or "" when it declares none.

    Builder Core's runners carry no ID of their own (its D63), so the envelope is the only source.
    Identification, not authorization: an ID is never guessed from an App ID, URL or hostname. An
    empty ID can't match an approval, so such a runner serves only where none is required.
    """
    return envelope.runner_id or ""


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

    def has_earlier(self, endpoint: str) -> bool:
        """Whether a runner on the version before this endpoint's profile may serve a job."""
        return endpoint in EARLIER

    def problems(self, endpoint: str, body: Any, *, earlier: bool = False) -> tuple[str, ...]:
        """How a request body breaks the entry's request schema; empty when it matches.

        Checked before payment. Each line says where and what the schema wants, never the value,
        so it can go back to the caller. Types the entry doesn't describe, such as a coding agent's
        custom tools, pass and the runner decides. Pure CPU, tens of milliseconds for a large
        conversation, so async callers run it in a worker thread.

        With `earlier`, the check is against the version before (`EARLIER`), which refuses what it
        doesn't describe: a body with no problems there may go to a runner that declares it.
        """
        profile = EARLIER[endpoint] if earlier else ENDPOINTS[endpoint]
        return validate_request(self.registry, profile, endpoint, body).problems

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
        earlier: bool = False,
    ) -> list[Runner]:
        """Runners serving `model` on `endpoint`, in their given order.

        With `earlier`, a runner that declares the version before the endpoint's profile serves
        too, where there is one (`EARLIER`) and no approval is asked for: an approval names the
        current profile. The caller sets it only for a body that version describes.

        A runner needs an approval where its endpoint is private, or for every endpoint when
        `approved_only` is set. Builder Core sends one path per job, so a runner whose envelope
        overrides the entry's path or method doesn't match. A runner reporting no free capacity
        doesn't match; one reporting none at all does only where approvals aren't required.
        """
        profiles = [ENDPOINTS[endpoint]]
        if earlier and not approved_only and endpoint in EARLIER:
            profiles.append(EARLIER[endpoint])
        runners: dict[int, Runner] = {}
        contract_runners = []
        for runner in candidates:
            envelope = read_envelope(runner.metadata)
            available = runner.capacity_available
            if isinstance(envelope, Ineligible) or (available is not None and available <= 0):
                continue
            if approved_only and available is None:
                continue
            contract = ContractRunner(
                orchestrator=runner.orchestrator_url or "",
                runner_id=runner_identity(envelope),
                app=runner.app,
                envelope=envelope,
                url=runner.url,
            )
            runners[id(contract)] = runner
            contract_runners.append(contract)
        selected: set[int] = set()
        for profile in profiles:
            operation = self.registry.operation(profile, endpoint)
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
                    selected.add(id(choice.runner))
        return [runners[id(c)] for c in contract_runners if id(c) in selected]
