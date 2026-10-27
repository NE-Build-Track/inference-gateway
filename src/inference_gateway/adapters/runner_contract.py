"""Shared deployment contract. Does not enable native routing by itself."""

import hashlib
import json
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

DIGEST = r"^[a-zA-Z0-9./:_-]+@sha256:[0-9a-f]{64}$"
PROFILES = {
    "chat_completions": {
        "path": "/v1/chat/completions",
        "profile": "openai.chat.v1",
        "usage": "chat.final.v1",
        "stream_usage": "include_usage_required",
    },
    "responses": {
        "path": "/v1/responses",
        "profile": "openai.responses.stateless.v1",
        "usage": "responses.final.v1",
        "stream_usage": "terminal",
    },
    "messages": {
        "path": "/v1/messages",
        "profile": "anthropic.messages.v1",
        "usage": "messages.events.v1",
        "stream_usage": "terminal",
    },
}


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Model(ContractModel):
    name: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    source: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    revision: str = Field(pattern=r"^(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})$")
    context_tokens: int = Field(ge=128, le=2097152)


class Runtime(ContractModel):
    name: Literal["vllm", "ollama", "sglang"]
    tool_parser: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")
    reasoning_parser: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")


class Deployment(ContractModel):
    contract_version: Literal[1] = 1
    runner_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    app: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
    runtime: Runtime
    model: Model
    endpoints: list[Literal["chat_completions", "responses", "messages"]] = Field(min_length=1)
    capacity: int = Field(ge=1, le=10000)
    public_url: str
    # Scaffolds are offchain and unpublished until operator acceptance.
    admission: Literal["candidate"] = "candidate"

    @model_validator(mode="after")
    def coherent(self) -> Self:
        u = urlsplit(self.public_url)
        if (
            u.scheme not in {"http", "https"}
            or not u.hostname
            or u.username
            or u.password
            or u.query
            or u.fragment
            or u.path not in {"", "/"}
        ):
            raise ValueError("public_url must be an HTTP(S) origin without credentials")
        if len(self.endpoints) != len(set(self.endpoints)):
            raise ValueError("Duplicate endpoint")
        if self.runtime.name == "ollama":
            if not self.model.revision.startswith("sha256:"):
                raise ValueError("Ollama requires the installed model manifest digest")
            if self.model.name != self.model.source:
                raise ValueError("Ollama source must equal the installed model name")
            if self.runtime.tool_parser or self.runtime.reasoning_parser:
                raise ValueError("Ollama owns its parsers; external parser flags are unsupported")
        elif len(self.model.revision) != 40:
            raise ValueError("vLLM/SGLang require an immutable Hugging Face commit")
        return self


class RuntimeIdentity(ContractModel):
    name: Literal["vllm", "ollama", "sglang"]
    image: str = Field(pattern=DIGEST)


class EndpointProfile(ContractModel):
    path: str
    profile: str
    usage: str
    stream_usage: Literal["include_usage_required", "terminal"]


class Limits(ContractModel):
    context_tokens: int = Field(ge=128, le=2097152)


class RunnerMetadata(ContractModel):
    inference_contract: Literal[1]
    model_name: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    runtime: RuntimeIdentity
    deployment_revision: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    endpoints: dict[str, EndpointProfile] = Field(min_length=1)
    limits: Limits
    admission: Literal["candidate"]

    @model_validator(mode="after")
    def known_profiles(self) -> Self:
        for name, endpoint in self.endpoints.items():
            if name not in PROFILES or endpoint.model_dump() != PROFILES[name]:
                raise ValueError("Unknown or mismatched endpoint profile")
        if len(self.model_dump_json().encode()) > 1024:
            raise ValueError("Metadata exceeds legacy 1024-byte limit")
        return self


def metadata(deployment: Deployment, revision: str, image: str) -> str:
    value = {
        "inference_contract": 1,
        "model_name": deployment.model.name,
        "runtime": {"name": deployment.runtime.name, "image": image},
        "deployment_revision": revision,
        "endpoints": {name: PROFILES[name] for name in deployment.endpoints},
        "limits": {"context_tokens": deployment.model.context_tokens},
        "admission": deployment.admission,
    }
    RunnerMetadata.model_validate(value)
    result = json.dumps(value, separators=(",", ":"), sort_keys=True)
    if len(result.encode()) > 1024:
        raise ValueError("Metadata exceeds legacy 1024-byte limit; reduce advertised endpoints")
    return result


def revision_for(value: object) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
