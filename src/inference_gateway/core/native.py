"""Opaque HTTP transport boundary; independent of generation DTOs and frameworks."""

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Protocol

from inference_gateway.core.models import Finished


@dataclass(frozen=True)
class NativeRequest:
    request_id: str
    model: str
    endpoint: str
    path: str
    query: bytes
    headers: tuple[tuple[str, str], ...]
    body: bytes
    streaming: bool


class NativeStream(Protocol):
    """Borrowed response. Owner must close even if no body bytes are consumed.

    Transport retains paid job correlation and disconnect/settlement semantics.
    EOF alone does not imply a valid provider terminal event or measured usage.
    """

    @property
    def status_code(self) -> int: ...
    @property
    def headers(self) -> tuple[tuple[str, str], ...]: ...
    @property
    def job_id(self) -> str: ...
    def __aiter__(self) -> AsyncGenerator[bytes, None]: ...
    def succeeded(self) -> bool:
        """Whether the answer met its wire profile's success rule; read once the body has ended."""
        ...

    async def complete(self) -> None: ...
    async def disconnected(self) -> None: ...
    async def aclose(self) -> None: ...


class NativeBackend(Protocol):
    def accepts(self, model: str) -> bool: ...
    def models(self) -> tuple[str, ...]: ...

    def supports(self, model: str, endpoint: str) -> bool:
        """Whether an operator ever approved this model for this endpoint.

        False is permanent until configuration changes, so callers must not suggest a retry.
        """
        ...

    def problems(self, request: NativeRequest) -> tuple[str, ...]:
        """How the request breaks its endpoint's wire profile; empty when it matches.

        A problem is the client's error. Lines never quote the request's values. Pure CPU that
        grows with the body, so callers run it off the event loop.
        """
        ...

    def models_for(self, endpoints: tuple[str, ...]) -> tuple[str, ...]:
        """Models with eligible supply right now for every one of these endpoints."""
        ...

    async def report_usage(self, job_id: str, usage: Finished) -> None: ...

    async def open(self, request: NativeRequest) -> NativeStream:
        """Admit before payment; forward original bytes without generation translation."""
        ...
