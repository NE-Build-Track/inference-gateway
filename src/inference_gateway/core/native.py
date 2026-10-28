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
    async def complete(self) -> None: ...
    async def disconnected(self) -> None: ...
    async def aclose(self) -> None: ...


class NativeBackend(Protocol):
    def accepts(self, model: str) -> bool: ...
    def models(self) -> tuple[str, ...]: ...

    async def report_usage(self, job_id: str, usage: Finished) -> None: ...

    async def open(self, request: NativeRequest) -> NativeStream:
        """Admit before payment; forward original bytes without generation translation."""
        ...
