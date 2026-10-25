"""Constrain the engine's discovered supply to configured apps and approved origins."""

from urllib.parse import urlsplit

from livepeer_builder import Runner
from livepeer_builder.protocols import DiscoverySource


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
