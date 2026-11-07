#!/usr/bin/env python3
"""Render a private, offchain candidate deployment for runner contract v1; starts nothing.

One manifest gives the Compose file, the readiness configuration, and for each registration a
standard `runner.toml`, its envelope and the node's `[[Runners]]` block. The standard's library
builds the envelope and block, and checks every block as the node reads it, before anything is
written.
"""

import argparse
import hashlib
import json
import re
import tomllib
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from livepeer_runner_contract import Envelope, Registry, parse_envelope
from livepeer_runner_contract.check import check_registrations, read_registrations
from livepeer_runner_contract.manifest import parse_manifest, render_block_toml, render_envelope
from pydantic import BaseModel, ConfigDict, Field, model_validator

from inference_gateway.backends.runner_contract import ENDPOINTS

DIGEST = r"^[a-zA-Z0-9./:_-]+@sha256:[0-9a-f]{64}$"
ROOT = Path(__file__).resolve().parents[1]
HEALTH_URL = "http://readiness:8080/healthz"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Model(ContractModel):
    name: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    # The Hugging Face repository, or the installed Ollama model.
    source: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    revision: str = Field(pattern=r"^(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})$")
    context_tokens: int = Field(ge=128, le=2097152)


class Runtime(ContractModel):
    name: Literal["vllm", "ollama", "sglang"]
    tool_parser: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")
    reasoning_parser: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")


class Price(ContractModel):
    """The wholesale price in USD: per hour of runner time, or a fixed price per call."""

    amount: float = Field(gt=0, allow_inf_nan=False)
    unit: Literal["hour", "fixed"]


class Deployment(ContractModel):
    runner_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    runtime: Runtime
    model: Model
    endpoints: list[Literal["chat_completions", "responses", "messages", "systemone"]] = Field(
        min_length=1
    )
    capacity: int = Field(ge=1, le=10000)
    price: Price
    public_url: str
    admission: Literal["candidate", "private"] = "candidate"
    # Whether the model reasons by default (the envelope's `reasoning`); None declares nothing.
    reasoning: Literal["on", "off"] | None = None

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
        elif self.reasoning == "on" and not self.runtime.reasoning_parser:
            raise ValueError(
                "A model that reasons needs a reasoning parser, or its thinking is text"
            )
        return self


def registrations(deployment: Deployment, registry: Registry) -> list[tuple[str, str, list[str]]]:
    """(ID, capability, endpoints) per registration: an App ID names one capability only."""
    groups: dict[str, list[str]] = {}
    for endpoint in deployment.endpoints:
        capability = registry.entry(ENDPOINTS[endpoint]).capabilities[0]
        groups.setdefault(capability, []).append(endpoint)
    if len(groups) == 1:
        [(capability, endpoints)] = groups.items()
        return [(deployment.runner_id, capability, endpoints)]
    return [(f"{deployment.runner_id}-{c}", c, e) for c, e in groups.items()]


def settings_of(deployment: Deployment) -> dict[str, str | int]:
    """The settings that change behavior, for the revision. Capacity and URLs stay out."""
    runtime = deployment.runtime
    settings: dict[str, str | int] = {
        "runtime": runtime.name,
        "context_tokens": deployment.model.context_tokens,
    }
    if runtime.tool_parser:
        settings["tool_parser"] = runtime.tool_parser
    if runtime.reasoning_parser:
        settings["reasoning_parser"] = runtime.reasoning_parser
    return settings


def manifest_toml(
    deployment: Deployment,
    registration: tuple[str, str, list[str]],
    *,
    url: str,
    images: list[str],
    runtime_image: str,
    grouped: bool,
) -> str:
    """The standard's `runner.toml` for one registration, which `runner-contract` reads too."""
    ident, capability, endpoints = registration
    model, runtime = deployment.model, deployment.runtime
    origin = "ollama" if runtime.name == "ollama" else "hf"
    q = json.dumps
    lines = [
        "[runner]",
        f"id = {q(ident)}",
        f"app = {q(capability)}",
        'mode = "single-shot"',
        f"capacity = {deployment.capacity}",
        f"url = {q(url)}",
        f"health_url = {q(HEALTH_URL)}",
    ]
    if deployment.admission == "private":
        lines.append('admission = "private"')
    lines += [
        "",
        "[price]",
        f"amount = {deployment.price.amount!r}",
        'currency = "usd"',
        f"unit = {q(deployment.price.unit)}",
        "",
        "[model]",
        f"name = {q(model.name)}",
        f"source = {q(origin + ':' + model.source)}",
        f"revision = {q(model.revision)}",
        "",
    ]
    for endpoint in endpoints:
        lines += ["[[endpoints]]", f"name = {q(endpoint)}", f"profile = {q(ENDPOINTS[endpoint])}"]
        lines.append("")
    lines += [
        "[limits]",
        f"context_tokens = {model.context_tokens}",
        "",
        "[runtime]",
        f"name = {q(runtime.name)}",
        f"image = {q(runtime_image.split('@', 1)[1])}",
        "",
    ]
    if grouped:
        lines += [
            "[capacity_group]",
            f"id = {q(deployment.runner_id)}",
            f"slots = {deployment.capacity}",
            "",
        ]
    if deployment.reasoning:
        lines += ["[reasoning]", f"default = {q(deployment.reasoning)}", ""]
    settings = ", ".join(f"{k} = {q(v)}" for k, v in sorted(settings_of(deployment).items()))
    lines += ["[revision]", f"images = {q(sorted(images))}", f"settings = {{ {settings} }}", ""]
    return "\n".join(lines)


def registration_block(
    text: str, ident: str, registry: Registry
) -> tuple[str, dict[str, object], str]:
    """The node block and envelope. The standard's tool writes `runner_id` into the envelope,
    since this node doesn't report it."""
    capabilities = registry.capabilities or {}
    manifest = parse_manifest(text, capabilities=capabilities)
    metadata = render_envelope(manifest)
    envelope = json.loads(metadata)
    if not isinstance(parse_envelope(metadata), Envelope) or envelope.get("runner_id") != ident:
        raise ValueError(f"{ident}: the envelope is not a valid runner_contract 1 envelope")
    block = render_block_toml(manifest)
    if tomllib.loads(block)["Runners"][0]["Metadata"] != metadata:
        raise AssertionError("the [[Runners]] block doesn't carry the envelope as written")
    problems = check_registrations(
        read_registrations(block, name=f"{ident}.toml"),
        registry=registry,
        capabilities=capabilities,
        manifest=manifest,
    )
    if problems:
        raise ValueError("; ".join(problems))
    return block, envelope, envelope["deployment_revision"]


def render(deployment: Deployment, output: Path, registry: Registry | None = None) -> dict:
    """Returns each registration's envelope, by registration ID."""
    registry = registry or Registry.load()
    lock = json.loads((ROOT / "images.lock.json").read_text())
    images = lock["images"]
    if any(not re.fullmatch(DIGEST, image) for image in images.values()):
        raise ValueError("All images must be digest-pinned")
    artifacts = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [Path(__file__), ROOT / "tooling/readiness.py"]
    }
    name = deployment.runtime.name
    model = deployment.model
    port = 11434 if name == "ollama" else 8000
    runtime_url = f"http://runtime:{port}"
    # The runtime and the readiness check that gates it; the node isn't part of the runner.
    revision_images = [images[name], images["readiness"]]
    plan = registrations(deployment, registry)
    rendered = []
    for registration in plan:
        text = manifest_toml(
            deployment,
            registration,
            url=runtime_url,
            images=revision_images,
            runtime_image=images[name],
            grouped=len(plan) > 1,
        )
        rendered.append(
            (registration[0], text, *registration_block(text, registration[0], registry))
        )

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
            command += [
                "--enable-prompt-tokens-details",
                "--max-num-seqs",
                str(deployment.capacity),
            ]
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
    output.mkdir(parents=True, exist_ok=True)

    def write(file: str, value: object) -> None:
        (output / file).write_text(json.dumps(value, indent=2) + "\n")

    write("compose.json", compose)
    write("manifest.json", deployment.model_dump())
    write("envelopes.json", {ident: envelope for ident, _, _, envelope, _ in rendered})
    write(
        "readiness.json",
        {
            "runtime": name,
            "url": runtime_url,
            "model": model.name,
            "model_revision": model.revision,
            # Every profile the registrations advertise is self-tested before health is ready.
            "endpoints": [
                {"name": e, "path": registry.operation(ENDPOINTS[e], e).path}
                for e in deployment.endpoints
            ],
        },
    )
    write(
        "release.json",
        {
            "registrations": {ident: revision for ident, _, _, _, revision in rendered},
            "registry_release": registry.release,
            "images": images,
            "source_hashes": artifacts,
            "acceptance": "pending",
            "admission": deployment.admission,
        },
    )
    for ident, text, block, _, _ in rendered:
        # Per registration, for `runner-contract check` and `conform`: the manifest and its block.
        (output / f"{ident}.runner.toml").write_text(text)
        (output / f"{ident}.block.toml").write_text(block)
    (output / "readiness.py").write_bytes((ROOT / "tooling/readiness.py").read_bytes())
    (output / "runners.toml").write_text("\n".join(block for _, _, block, _, _ in rendered))
    (output / "orchestrator.toml").write_text(
        'Listen = "0.0.0.0:8935"\nMetricsListen = "127.0.0.1:8936"\n'
        + f"ServiceURL = {json.dumps(deployment.public_url)}\n"
        + 'RunnerConfig = "/etc/livepeer/runners.toml"\nNetwork = "offchain"\n'
        + 'RunnerGrants = ["runtime"]\nHealthGrants = ["readiness:8080"]\n'
    )
    return {ident: envelope for ident, _, _, envelope, _ in rendered}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = inside_working_directory(args.output)
    deployment = Deployment.model_validate_json(args.manifest.read_text())
    rendered = render(deployment, output)
    print(f"Rendered {len(rendered)} registration(s) into {output}; acceptance pending")


def inside_working_directory(path: Path) -> Path:
    """The bundle overwrites files, so --output must stay under the directory it runs in."""
    base = Path.cwd().resolve()
    resolved = (base / path).resolve()
    if not resolved.is_relative_to(base):
        raise SystemExit(f"--output must be inside the working directory ({base}), not {resolved}")
    return resolved


if __name__ == "__main__":
    main()
