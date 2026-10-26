from collections.abc import AsyncGenerator
from typing import Protocol

from inference_gateway.core.models import Event, Generate


class BackendError(Exception):
    """A backend failure whose details must not leak to clients."""

    error_code: str | None = None
    status_code = 502
    public_message = "Backend generation failed"


class UnsupportedFeature(BackendError):
    status_code = 400
    error_code = "unsupported_feature"
    public_message = "Unsupported feature for selected backend"


class UnsupportedResponse(BackendError):
    error_code = "unsupported_response"
    public_message = "Unsupported Response"


class UnknownModel(BackendError):
    status_code = 400
    public_message = "Unknown configured model"


class BackendUnavailable(BackendError):
    status_code = 503
    public_message = "Generation backend unavailable"


class BackendConflict(BackendError):
    status_code = 409
    public_message = "Generation operation already dispatched"


class BackendDeadline(BackendError):
    status_code = 504
    public_message = "Generation deadline exceeded"


class Backend(Protocol):
    def stream(self, request: Generate) -> AsyncGenerator[Event, None]:
        """Yield ordered blocks then exactly one Finished; release resources on close.

        Block starts are indexed consecutively from zero; client tool blocks may overlap.
        Tool starts contain an empty
        arguments string; deltas form a JSON object. Never yield after Finished.
        The application borrows this backend; its creator owns its lifetime.
        """
        ...
