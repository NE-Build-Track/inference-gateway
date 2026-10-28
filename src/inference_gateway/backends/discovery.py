"""Constrain the engine's discovered supply to configured apps and approved origins."""

import json
from collections.abc import Sequence
from urllib.parse import urlsplit

from livepeer_builder import JobRequest, NoCapacity, Runner
from livepeer_builder.protocols import DiscoverySource, SelectionPolicy

from inference_gateway.adapters.runner_contract import RunnerMetadata


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
    def __init__(
        self,
        inner: DiscoverySource,
        apps: frozenset[str],
        approved: tuple[str, ...],
        *,
        native_routes: frozenset[tuple[str, str]] | None = None,
    ) -> None:
        self._inner = inner
        self._apps = apps
        self._native_routes = native_routes
        self._origins = frozenset(origin(url) for url in approved)

    async def fetch(self) -> tuple[Runner, ...]:
        selected: list[Runner] = []
        for runner in await self._inner.fetch():
            if runner.app not in self._apps:
                continue
            if self._origins:
                try:
                    address = origin(runner.orchestrator_url or "")
                except ValueError:
                    continue
                if address not in self._origins:
                    continue
            native = (
                self._native_routes is None
                or (runner.app, model_name(runner)) in self._native_routes
            )
            normalized = normalize_native_identity(runner) if native else runner
            if normalized is not None:
                selected.append(normalized)
        return tuple(selected)


def native_runner_id(runner: Runner, metadata: RunnerMetadata) -> str | None:
    """Resolve a declaration, not an authorization; never infer identity from a URL."""
    if metadata.runner_id is not None:
        if runner.runner_id and runner.runner_id != metadata.runner_id:
            return None
        return metadata.runner_id
    return runner.runner_id or None


def normalize_native_identity(runner: Runner) -> Runner | None:
    if len((runner.metadata or "").encode()) > 1024:
        return (
            runner  # Native selection rejects oversized declarations; leave legacy routes intact.
        )
    try:
        value = json.loads(runner.metadata or "")
    except (ValueError, TypeError):
        return runner  # Legacy validated routes can carry opaque metadata.
    if not isinstance(value, dict) or "inference_contract" not in value:
        return runner
    try:
        metadata = RunnerMetadata.model_validate_json(runner.metadata or "")
    except ValueError:
        return None
    identity = native_runner_id(runner, metadata)
    if identity is None:
        return None
    return runner.model_copy(update={"runner_id": identity})


def model_name(runner: Runner) -> str | None:
    """Metadata is untrusted; app names and opaque text are never model identities."""
    try:
        metadata = json.loads(runner.metadata or "")
    except (ValueError, TypeError):
        return None
    value = metadata.get("model_name") if isinstance(metadata, dict) else None
    return value if isinstance(value, str) and value and value == value.strip() else None


class ModelSelection:
    """Filter before the engine ranks/retries; never cross the requested app boundary."""

    def __init__(self, inner: "SelectionPolicy") -> None:
        self._inner = inner

    def order(self, candidates: "Sequence[Runner]", request: "JobRequest") -> "Sequence[Runner]":
        selected = [
            r
            for r in candidates
            if r.app == request.app
            and model_name(r) == request.model
            and (r.capacity_available is None or r.capacity_available > 0)
        ]
        ordered = self._inner.order(selected, request)
        if not ordered:
            raise NoCapacity("No eligible model runner")
        return ordered


class ProtocolSelection:
    def __init__(self, validated: SelectionPolicy, native: SelectionPolicy) -> None:
        self.validated, self.native = validated, native

    def order(self, candidates: Sequence[Runner], request: JobRequest) -> Sequence[Runner]:
        policy = self.native if request.capability == "native.http.v1" else self.validated
        return policy.order(candidates, request)
