"""Version two declarations for workflow-backed media runners; v1 is unchanged."""

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from inference_gateway.adapters.runner_contract import (
    DIGEST,
    PROFILES,
    ContractModel,
    EndpointProfile,
    RunnerMetadata,
)

MEDIA_PROFILES = {
    name: {
        "path": path,
        "profile": profile,
        "usage": "media.measured.v1",
        "stream_usage": "unsupported",
    }
    for name, path, profile in (
        ("images_generations", "/v1/images/generations", "openai.images.v1"),
        ("images_edits", "/v1/images/edits", "openai.images.edits.v1"),
        ("audio_speech", "/v1/audio/speech", "openai.audio.speech.v1"),
        ("audio_transcriptions", "/v1/audio/transcriptions", "openai.audio.transcriptions.v1"),
        ("videos", "/v1/videos", "openai.videos.v1"),
        ("image_transformations", "/v1/image-transformations", "gateway.image.transformations.v1"),
        ("music_generations", "/v1/music/generations", "gateway.music.v1"),
        ("meshes_generations", "/v1/meshes/generations", "gateway.meshes.v1"),
    )
}


class CapabilityRuntime(ContractModel):
    name: Literal["comfystream"]
    image: str = Field(pattern=DIGEST)


class CapacityGroup(ContractModel):
    id: str = Field(min_length=1, max_length=64)
    slots: int = Field(ge=1)


class CapabilityMetadata(ContractModel):
    inference_contract: Literal[2]
    runner_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    model_name: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    runtime: CapabilityRuntime
    deployment_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    endpoints: dict[str, EndpointProfile] = Field(min_length=1)
    capacity_group: CapacityGroup
    limits: dict[str, int] = Field(default_factory=dict)
    admission: Literal["candidate"]

    @model_validator(mode="after")
    def coherent(self) -> Self:
        for name, endpoint in self.endpoints.items():
            if endpoint.model_dump() != {**PROFILES, **MEDIA_PROFILES}.get(name):
                raise ValueError("Unknown or mismatched capability endpoint profile")
        if any(value < 0 for value in self.limits.values()):
            raise ValueError("Limits cannot be negative")
        if len(self.model_dump_json(exclude_none=True).encode()) > 1024:
            raise ValueError("Metadata exceeds legacy 1024-byte limit")
        return self


def parse_metadata(value: dict[str, Any]) -> RunnerMetadata | CapabilityMetadata:
    if value.get("inference_contract") == 2:
        return CapabilityMetadata.model_validate(value)
    return RunnerMetadata.model_validate(value)
