"""Constrain the engine's discovered supply to configured apps and approved origins."""

from collections.abc import Sequence
from urllib.parse import urlsplit

from livepeer_builder import JobRequest, NoCapacity, Runner
from livepeer_builder.protocols import DiscoverySource, SelectionPolicy


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
        self, inner: DiscoverySource, apps: frozenset[str], approved: tuple[str, ...]
    ) -> None:
        self._inner = inner
        self._apps = apps
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
            selected.append(runner)
        return tuple(selected)


def model_name(runner: Runner) -> str | None:
    """Metadata is untrusted; app names and opaque text are never model identities."""
    import json

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
