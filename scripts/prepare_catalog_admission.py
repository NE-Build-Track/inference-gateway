"""Compile reviewed discovery/OpenAPI exports into exact, expiring network admission."""

import argparse
import copy
import json
import time
from pathlib import Path
from typing import Any

from inference_gateway.adapters.runner_contract_v2 import RunnerMetadataV2
from inference_gateway.backends.discovery import origin
from inference_gateway.config import NativeApproval


def compile_catalog(
    deployment: dict[str, Any],
    inventory: list[dict[str, Any]],
    spec: dict[str, Any],
    expires: float,
) -> dict[str, Any]:
    if expires <= time.time():
        raise ValueError("Admission must expire in the future")
    schema = copy.deepcopy(spec)
    schema["paths"] = {}
    approvals, routes = [], []
    for capability, settings in deployment["models"].items():
        candidates = []
        for entry in inventory:
            for runner in entry.get("runners", []):
                if runner.get("app") == settings["app"]:
                    metadata = RunnerMetadataV2.model_validate_json(runner["metadata"])
                    if (
                        metadata.runtime.image == deployment["image"]
                        and metadata.runner_id == settings["runner_id"]
                    ):
                        origin(entry["address"])  # Validate without exposing a runner address.
                        candidates.append((entry["address"], metadata))
        if len(candidates) != 1:
            raise ValueError("Require one exact deployed runner for each reviewed model")
        orchestrator, metadata = candidates[0]
        routes.append({"name": metadata.model_name, "app": settings["app"], "mode": "native"})
        for operation, endpoint in metadata.endpoints.items():
            path = "/catalog/" + capability + endpoint.path
            definition = copy.deepcopy(spec["paths"][path])
            body = definition["post"]["requestBody"]["content"]
            for content in body.values():
                if (
                    content["schema"].get("properties", {}).get("model", {}).get("const")
                    != metadata.model_name
                ):
                    raise ValueError("Schema and deployed model differ")
            definition["post"]["x-livepeer"] = {
                "app": settings["app"],
                "model": metadata.model_name,
                "deployment_revision": metadata.deployment_revision,
                "path": endpoint.path,
            }
            schema["paths"][path] = definition
            approval = NativeApproval(
                orchestrator=orchestrator,
                runner_id=metadata.runner_id,
                app=settings["app"],
                model=metadata.model_name,
                deployment_revision=metadata.deployment_revision,
                endpoint=operation,
                expires_at=expires,
            )
            approvals.append(approval.model_dump())
    return {"routes": routes, "approvals": approvals, "openapi": schema}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--discovery", type=Path, required=True)
    parser.add_argument("--openapi", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hours", type=int, default=24, choices=range(1, 169))
    args = parser.parse_args()
    result = compile_catalog(
        json.loads(args.deployment.read_text(encoding="utf-8")),
        json.loads(args.discovery.read_text(encoding="utf-8")),
        json.loads(args.openapi.read_text(encoding="utf-8")),
        time.time() + args.hours * 3600,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    for key, filename in (
        ("routes", "model-routes.json"),
        ("approvals", "native-approvals.json"),
        ("openapi", "capabilities.openapi.json"),
    ):
        (args.output / filename).write_text(
            json.dumps(result[key], indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
