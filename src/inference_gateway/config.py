"""Configuration values. Only the application composition root loads the environment."""

import math
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


@dataclass(frozen=True)
class Settings:
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = 300
    backend: Literal["fake", "livepeer"] = "fake"
    accounting_db: str | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        if self.accounting_db and self.api_key:
            raise ValueError("Registry mode cannot also use a legacy shared key")
        if self.api_key == "":
            raise ValueError("api_key cannot be empty")
        if self.backend not in {"fake", "livepeer"}:
            raise ValueError("backend must be fake or livepeer")


class ModelRoute(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1)
    app: str = Field(min_length=1)
    upstream_model: str | None = Field(default=None, min_length=1)
    # The initial Qwen runner supports this vLLM extension. Other routes can omit it.
    disable_thinking: bool = True


class LivepeerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    application_id: str = Field(default="endpoint-gateway", min_length=1)
    actor_id: str = Field(default="shared-application", min_length=1)
    routes: tuple[ModelRoute, ...] = (
        ModelRoute(name="qwen3.8-27b", app="blueclaw/chat/qwen3.8-27b"),
    )
    approved_orchestrators: tuple[str, ...] = ()
    timeout_seconds: float = Field(default=300, gt=0, le=3600)
    first_byte_timeout_seconds: float = Field(default=60, gt=0, le=3600)
    idle_timeout_seconds: float = Field(default=60, gt=0, le=3600)
    max_attempts: int = Field(default=3, ge=1, le=10)
    max_event_bytes: int = Field(default=1048576, gt=0)
    max_output_bytes: int = Field(default=8388608, gt=0)

    @model_validator(mode="after")
    def unique_routes(self) -> "LivepeerSettings":
        if not self.routes or len({r.name for r in self.routes}) != len(self.routes):
            raise ValueError("Configure at least one route with unique names")
        return self
