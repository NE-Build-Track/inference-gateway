#!/usr/bin/env python3
"""Render a private, offchain candidate deployment; never starts containers."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from inference_gateway.adapters.runner_contract import (
    DIGEST,
    Deployment,
    metadata,
    revision_for,
)

ROOT = Path(__file__).resolve().parents[1]


def render(deployment: Deployment, output: Path) -> dict:
    lock = json.loads((ROOT / "images.lock.json").read_text())
    images = lock["images"]
    if any(not re.fullmatch(DIGEST, image) for image in images.values()):
        raise ValueError("All images must be digest-pinned")
    artifacts = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [Path(__file__), ROOT / "tooling/readiness.py", ROOT / "contract/profiles.json"]
    }
    artifacts["runner_contract.py"] = hashlib.sha256(
        (ROOT.parent / "src/inference_gateway/adapters/runner_contract.py").read_bytes()
    ).hexdigest()
    revision = revision_for(
        {"manifest": deployment.model_dump(), "images": images, "code": artifacts}
    )
    name = deployment.runtime.name
    model = deployment.model
    port = 11434 if name == "ollama" else 8000
    command = []
    if name != "ollama":
        command = [
            "--model" if name == "vllm" else "--model-path",
            model.source,
            "--revision",
            model.revision,
            "--served-model-name",
            model.name,
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
            "--max-model-len" if name == "vllm" else "--context-length",
            str(model.context_tokens),
        ]
        if name == "vllm":
            command += ["--enable-prompt-tokens-details"]
        else:
            command += ["--enable-cache-report"]
        if deployment.runtime.tool_parser:
            if name == "vllm":
                command += ["--enable-auto-tool-choice"]
            command += ["--tool-call-parser", deployment.runtime.tool_parser]
        if deployment.runtime.reasoning_parser:
            command += ["--reasoning-parser", deployment.runtime.reasoning_parser]
    runtime = {
        "image": images[name],
        "platform": "linux/amd64",
        "shm_size": "2gb",
        "volumes": [
            "model-cache:/root/.ollama"
            if name == "ollama"
            else "model-cache:/root/.cache/huggingface"
        ],
        "deploy": {
            "resources": {
                "reservations": {
                    "devices": [{"driver": "nvidia", "count": 1, "capabilities": ["gpu"]}]
                }
            }
        },
    }
    if name == "sglang":
        runtime["entrypoint"] = ["python3", "-m", "sglang.launch_server"]
    if name == "ollama":
        runtime["command"] = ["serve"]
        runtime["environment"] = {
            "OLLAMA_HOST": "0.0.0.0:11434",
            "OLLAMA_CONTEXT_LENGTH": str(model.context_tokens),
        }
    else:
        runtime["command"] = command
        runtime["environment"] = {"HF_TOKEN": "${HF_TOKEN:-}"}
    compose = {
        "name": "runner-" + deployment.runner_id,
        "services": {
            "runtime": runtime,
            "readiness": {
                "image": images["readiness"],
                "command": ["python", "/app/readiness.py"],
                "volumes": [
                    "./readiness.py:/app/readiness.py:ro",
                    "./readiness.json:/app/readiness.json:ro",
                ],
                "depends_on": ["runtime"],
                "read_only": True,
                "environment": {"PYTHONDONTWRITEBYTECODE": "1"},
            },
            "orchestrator": {
                "image": images["node"],
                "platform": "linux/amd64",
                "command": ["orchestrator", "--config", "/etc/livepeer/orchestrator.toml"],
                "volumes": [
                    "./orchestrator.toml:/etc/livepeer/orchestrator.toml:ro",
                    "./runners.toml:/etc/livepeer/runners.toml:ro",
                    "node-data:/home/livepeer/.lpData",
                ],
                "ports": ["127.0.0.1:8935:8935"],
                "depends_on": ["readiness"],
            },
        },
        "volumes": {"model-cache": {}, "node-data": {}},
    }
    encoded = metadata(deployment, revision, images[name])
    runner = {
        "ID": deployment.runner_id,
        "App": deployment.app,
        "RunnerURL": f"http://runtime:{port}",
        "Mode": "single-shot",
        "Status": "idle",
        "Capacity": deployment.capacity,
        "Metadata": encoded,
        "HealthURL": "http://readiness:8080/healthz",
        "HealthCode": 200,
    }
    output.mkdir(parents=True, exist_ok=True)

    def write(name: str, value: object) -> None:
        (output / name).write_text(json.dumps(value, indent=2) + "\n")

    write("compose.json", compose)
    write("manifest.json", deployment.model_dump())
    write("metadata.json", json.loads(encoded))
    write(
        "readiness.json",
        {
            "runtime": name,
            "url": f"http://runtime:{port}",
            "model": model.name,
            "model_revision": model.revision,
        },
    )
    write(
        "release.json",
        {
            "deployment_revision": revision,
            "images": images,
            "source_hashes": artifacts,
            "acceptance": "pending",
            "admission": "candidate",
        },
    )
    (output / "readiness.py").write_bytes((ROOT / "tooling/readiness.py").read_bytes())
    (output / "runners.toml").write_text(
        "[[Runners]]\n" + "\n".join(f"{k} = {json.dumps(v)}" for k, v in runner.items()) + "\n"
    )
    (output / "orchestrator.toml").write_text(
        'Listen = "0.0.0.0:8935"\nMetricsListen = "127.0.0.1:8936"\n'
        + f"ServiceURL = {json.dumps(deployment.public_url)}\n"
        + 'RunnerConfig = "/etc/livepeer/runners.toml"\nNetwork = "offchain"\n'
        + 'RunnerGrants = ["runtime"]\nHealthGrants = ["readiness:8080"]\n'
    )
    return json.loads(encoded)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    deployment = Deployment.model_validate_json(args.manifest.read_text())
    render(deployment, args.output)
    print(f"Rendered candidate into {args.output}; idle registration, acceptance pending")


if __name__ == "__main__":
    main()
