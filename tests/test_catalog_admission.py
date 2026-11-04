import copy
import json
import runpy
import time
from pathlib import Path

import pytest

from inference_gateway.adapters.runner_contract_v2 import MEDIA_PROFILES

compile_catalog = runpy.run_path(
    str(Path(__file__).parents[1] / "scripts/prepare_catalog_admission.py")
)["compile_catalog"]


def artifacts():
    metadata = {
        "inference_contract": 2,
        "runner_id": "image-one",
        "model_name": "exact-model",
        "runtime": {"name": "comfystream", "image": "example@sha256:" + "a" * 64},
        "deployment_revision": "sha256:" + "b" * 64,
        "admission": "candidate",
        "capacity_group": {"id": "gpu0", "slots": 1},
        "endpoints": {"images_generations": MEDIA_PROFILES["images_generations"]},
    }
    deployment = {
        "image": metadata["runtime"]["image"],
        "models": {"image": {"app": "image-app", "runner_id": "image-one", "price": "0.02"}},
    }
    inventory = [
        {
            "address": "https://orch.example:8936",
            "runners": [{"app": "image-app", "metadata": json.dumps(metadata)}],
        }
    ]
    spec = {
        "openapi": "3.1.0",
        "paths": {
            "/catalog/image/v1/images/generations": {
                "post": {
                    "operationId": "image",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"model": {"const": "exact-model"}},
                                }
                            }
                        }
                    },
                }
            }
        },
    }
    return deployment, inventory, spec


def test_admission_binds_exact_identity_expiry_and_schema_without_modifying_source():
    deployment, inventory, spec = artifacts()
    before = copy.deepcopy(spec)
    expiry = time.time() + 3600
    compiled = compile_catalog(deployment, inventory, spec, expiry)
    approval = compiled["approvals"][0]
    assert approval["orchestrator"] == "https://orch.example:8936"
    assert approval["deployment_revision"] == "sha256:" + "b" * 64
    assert approval["expires_at"] == expiry
    binding = compiled["openapi"]["paths"]["/catalog/image/v1/images/generations"]["post"][
        "x-livepeer"
    ]
    assert binding["path"] == "/v1/images/generations" and binding["model"] == "exact-model"
    assert spec == before


def test_changed_runtime_or_schema_cannot_reuse_admission():
    deployment, inventory, spec = artifacts()
    deployment["image"] = "other@sha256:" + "c" * 64
    with pytest.raises(ValueError, match="one exact deployed runner"):
        compile_catalog(deployment, inventory, spec, time.time() + 3600)
    deployment, inventory, spec = artifacts()
    spec["paths"]["/catalog/image/v1/images/generations"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]["properties"]["model"]["const"] = "other"
    with pytest.raises(ValueError, match="Schema and deployed model differ"):
        compile_catalog(deployment, inventory, spec, time.time() + 3600)
