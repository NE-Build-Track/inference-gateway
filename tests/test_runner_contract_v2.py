import json

import pytest
from livepeer_builder import JobRequest, NoCapacity
from livepeer_builder.services.discovery import MostCapacityFirst
from livepeer_builder.testing import runner
from pydantic import ValidationError

from inference_gateway.adapters.runner_contract import PROFILES
from inference_gateway.adapters.runner_contract_v2 import (
    MEDIA_PROFILES,
    RunnerMetadataV2,
    parse_metadata,
)
from inference_gateway.backends.discovery import normalize_native_identity
from inference_gateway.backends.native_selection import Admission, NativeSelection


def declaration(endpoint="images_generations"):
    return {
        "inference_contract": 2,
        "runner_id": "shared-gpu-model",
        "model_name": "model/one",
        "runtime": {"name": "comfystream", "image": "example/runtime@sha256:" + "a" * 64},
        "deployment_revision": "sha256:" + "b" * 64,
        "endpoints": {endpoint: {**PROFILES, **MEDIA_PROFILES}[endpoint]},
        "capacity_group": {"id": "host-gpu0", "slots": 1},
        "admission": "candidate",
    }


def test_media_declaration_has_shared_capacity_without_context_token_limit():
    raw = declaration()
    parsed = parse_metadata(raw)
    assert isinstance(parsed, RunnerMetadataV2) and parsed.limits == {}
    assert parsed.capacity_group.id == "host-gpu0"
    for key, value in (("slots", 0), ("id", "")):
        invalid = declaration()
        invalid["capacity_group"][key] = value
        with pytest.raises(ValidationError):
            parse_metadata(invalid)


def test_v2_discovery_preserves_identity_and_requires_external_text_admission():
    metadata = declaration("chat_completions")
    supply = runner(
        "app",
        orchestrator_url="https://orch",
        metadata=json.dumps(metadata, separators=(",", ":")),
        capacity_available=1,
    ).model_copy(update={"runner_id": ""})
    normalized = normalize_native_identity(supply)
    assert normalized is not None and normalized.runner_id == metadata["runner_id"]
    approval = Admission(
        "https://orch",
        metadata["runner_id"],
        "app",
        "model/one",
        metadata["deployment_revision"],
        "chat_completions",
        200,
    )
    policy = NativeSelection(MostCapacityFirst(), lambda: (), now=lambda: 100)
    request = JobRequest(app="app", model="model/one", path="/v1/chat/completions")
    with pytest.raises(NoCapacity):
        policy.order([normalized], request)
    admitted = NativeSelection(MostCapacityFirst(), lambda: (approval,), now=lambda: 100)
    assert admitted.order([normalized], request) == [normalized]
    mismatched = normalized.model_copy(update={"runner_id": "someone-else"})
    assert normalize_native_identity(mismatched) is None
