"""Constrain the engine's discovered supply to approved origins and runner contract v1 envelopes."""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from livepeer_builder import JobRequest, NoCapacity, Runner
from livepeer_builder.protocols import DiscoverySource, SelectionPolicy
from livepeer_runner_contract import Ineligible, RunnerKey, log_ignored

from inference_gateway.backends.runner_contract import (
    RunnerContract,
    read_envelope,
    runner_identity,
)

if TYPE_CHECKING:
    from inference_gateway.backends.approvals import Approvals

NATIVE = "native.http.v1"


def origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Orchestrator allowlist entries must be HTTP(S) origins")
    return (
        parsed.scheme,
        parsed.hostname.lower(),
        parsed.port or (443 if parsed.scheme == "https" else 80),
    )


class FilteredDiscovery:
    """Keeps runners of the routes' capabilities, from approved origins, with a valid envelope.

    Every runner whose record isn't a valid envelope is logged once with the reason and a link to
    the standard, whatever its capability. A bad record excludes only its runner.
    """

    def __init__(
        self,
        inner: DiscoverySource,
        capabilities: frozenset[str],
        approved: tuple[str, ...],
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self._inner = inner
        self._capabilities = capabilities
        self._origins = frozenset(origin(url) for url in approved)
        self._logger = logger or logging.getLogger("inference_gateway")

    async def fetch(self) -> tuple[Runner, ...]:
        selected: list[Runner] = []
        for runner in await self._inner.fetch():
            if self._origins:
                try:
                    address = origin(runner.orchestrator_url or "")
                except ValueError:
                    continue
                if address not in self._origins:
                    continue
            envelope = read_envelope(runner.metadata)
            key = RunnerKey(runner.orchestrator_url or "", runner.runner_id or "")
            if isinstance(envelope, Ineligible):
                log_ignored(self._logger, key, envelope, level=logging.INFO)
                continue
            if runner.app not in self._capabilities:
                continue
            identity = runner_identity(runner, envelope)
            if identity is None:
                log_ignored(self._logger, key, "its envelope's runner_id differs from the node's")
                continue
            if identity and not runner.runner_id:
                runner = runner.model_copy(update={"runner_id": identity})
            selected.append(runner)
        return tuple(selected)


class ContractSelection:
    """Builder Core's selection hook: only runners the contract and approvals admit, before Core
    ranks, pays or fails over. Validated jobs need approval only for private runners; native jobs
    need it for every runner. A ranking policy must not widen the admitted set.
    """

    def __init__(
        self, inner: SelectionPolicy, contract: RunnerContract, approvals: "Approvals"
    ) -> None:
        self._inner = inner
        self._contract = contract
        self._approvals = approvals

    def order(self, candidates: Sequence[Runner], request: JobRequest) -> Sequence[Runner]:
        endpoint = self._contract.endpoint_for(request.path)
        if endpoint is None or request.method != "POST":
            raise NoCapacity("No admitted endpoint")
        eligible = self._contract.eligible(
            candidates,
            capability=request.app,
            model=request.model or "",
            endpoint=endpoint,
            approvals=self._approvals.approver(request.model or ""),
            approved_only=request.capability == NATIVE,
        )
        if not eligible:
            raise NoCapacity("No eligible runner")
        ordered = [r for r in self._inner.order(eligible, request) if r in eligible]
        if not ordered:
            raise NoCapacity("No eligible runner")
        return ordered
