"""Constrain the engine's discovered supply to configured apps and approved origins."""

import json
from collections.abc import Sequence
from urllib.parse import urlsplit

from livepeer_builder import JobRequest, NoCapacity, Runner
from livepeer_builder.protocols import DiscoverySource, SelectionPolicy

from inference_gateway.adapters.runner_contract import PROFILES, RunnerMetadata


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
            if not native or declares_native_identity(runner):
                selected.append(runner)
        return tuple(selected)


def native_runner_id(metadata: RunnerMetadata) -> str | None:
    """Resolve a declaration, not an authorization; never infer identity from a URL.

    Builder Core's runners carry no top-level ID (its D63); validated metadata is the only source.
    """
    return metadata.runner_id


def declares_native_identity(runner: Runner) -> bool:
    """Keep a runner unless its contract metadata is invalid or declares no runner ID."""
    if len((runner.metadata or "").encode()) > 1024:
        return True  # Native selection rejects oversized declarations; leave legacy routes intact.
    try:
        value = json.loads(runner.metadata or "")
    except (ValueError, TypeError):
        return True  # Legacy validated routes can carry opaque metadata.
    if not isinstance(value, dict) or "inference_contract" not in value:
        return True
    try:
        metadata = RunnerMetadata.model_validate_json(runner.metadata or "")
    except ValueError:
        return False
    return native_runner_id(metadata) is not None


def model_name(runner: Runner) -> str | None:
    """Metadata is untrusted; app names and opaque text are never model identities."""
    try:
        metadata = json.loads(runner.metadata or "")
    except (ValueError, TypeError):
        return None
    value = metadata.get("model_name") if isinstance(metadata, dict) else None
    return value if isinstance(value, str) and value and value == value.strip() else None


def declared_endpoints(runner: Runner) -> frozenset[str] | None:
    """Endpoints in contract metadata, or None for the model-name-only form validated routes use."""
    try:
        metadata = json.loads(runner.metadata or "")
    except (ValueError, TypeError):
        return None
    endpoints = metadata.get("endpoints") if isinstance(metadata, dict) else None
    return frozenset(endpoints) if isinstance(endpoints, dict) else None


def declares_path(runner: Runner, path: str) -> bool:
    """Model-name-only metadata declares nothing, so only an explicit contract can exclude."""
    declared = declared_endpoints(runner)
    return declared is None or any(
        name in PROFILES and PROFILES[name]["path"] == path for name in declared
    )


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
            and declares_path(r, request.path)
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
