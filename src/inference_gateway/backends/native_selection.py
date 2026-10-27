"""Operator-owned native admission, applied before Builder Core ranks or pays."""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from livepeer_builder import JobRequest, NoCapacity, Runner
from livepeer_builder.protocols import SelectionPolicy
from pydantic import ValidationError

from inference_gateway.adapters.runner_contract import PROFILES, RunnerMetadata
from inference_gateway.backends.discovery import native_runner_id, origin


@dataclass(frozen=True)
class Admission:
    """Trusted evidence, supplied by the operator, never by runner metadata.

    Snapshot replacement revokes approvals for subsequent selections. In-flight jobs
    retain their existing settlement lifecycle. Expiry is UTC epoch seconds.
    """

    orchestrator: str
    runner_id: str
    app: str
    model: str
    deployment_revision: str
    endpoint: str
    expires_at: float


class NativeSelection:
    def __init__(
        self,
        inner: SelectionPolicy,
        approvals: Callable[[], tuple[Admission, ...]],
        *,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._inner = inner
        self._approvals = approvals
        self._now = now

    def order(self, candidates: Sequence[Runner], request: JobRequest) -> Sequence[Runner]:
        endpoint = next((k for k, p in PROFILES.items() if p["path"] == request.path), None)
        if endpoint is None or request.method != "POST":
            raise NoCapacity("No admitted native endpoint")
        approvals, now = self._approvals(), self._now()
        eligible = []
        for runner in candidates:
            if (
                runner.app != request.app
                or runner.capacity_available is None
                or runner.capacity_available <= 0
            ):
                continue
            raw = runner.metadata or ""
            if len(raw.encode()) > 1024:
                continue
            try:
                meta = RunnerMetadata.model_validate_json(raw)
                address = origin(runner.orchestrator_url or "")
            except (ValueError, ValidationError):
                continue
            identity = native_runner_id(runner, meta)
            if (
                identity is None
                or meta.model_name != request.model
                or endpoint not in meta.endpoints
            ):
                continue
            for approval in approvals:
                try:
                    matches = origin(approval.orchestrator) == address
                except ValueError:
                    continue
                if (
                    matches
                    and approval.expires_at > now
                    and approval.runner_id == identity
                    and approval.app == request.app
                    and approval.model == request.model
                    and approval.deployment_revision == meta.deployment_revision
                    and approval.endpoint == endpoint
                ):
                    eligible.append(runner)
                    break
        if not eligible:
            raise NoCapacity("No admitted native runner")
        # A ranking policy must not widen the already admitted set.
        ordered = [r for r in self._inner.order(eligible, request) if r in eligible]
        if not ordered:
            raise NoCapacity("No admitted native runner")
        return ordered
