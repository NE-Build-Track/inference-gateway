"""Offline acceptance for the versioned runner bundle, without Docker/GPU/network."""

import importlib.util
import json
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from inference_gateway.adapters.runner_contract import PROFILES, Deployment, RunnerMetadata

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "live-runner"


def module(name):
    spec = importlib.util.spec_from_file_location(name, BUNDLE / "tooling" / f"{name}.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


render = module("render").render
check_ready = module("readiness").check


def manifest(family="vllm"):
    value = json.loads((BUNDLE / "runtimes" / family / "manifest.example.json").read_text())
    value["model"]["revision"] = "sha256:" + "a" * 64 if family == "ollama" else "a" * 40
    return value


@pytest.mark.parametrize("family", ["vllm", "ollama", "sglang"])
def test_bundle_identity_config_and_health_are_aligned(tmp_path, family):
    deployment = Deployment.model_validate(manifest(family))
    advertised = render(deployment, tmp_path)
    compose = json.loads((tmp_path / "compose.json").read_text())
    runner = tomllib.loads((tmp_path / "runners.toml").read_text())["Runners"][0]
    node = tomllib.loads((tmp_path / "orchestrator.toml").read_text())
    health = json.loads((tmp_path / "readiness.json").read_text())
    runtime = compose["services"]["runtime"]
    assert json.loads(runner["Metadata"]) == advertised
    assert advertised["runner_id"] == runner["ID"] == deployment.runner_id
    assert advertised["model_name"] == health["model"] == deployment.model.name
    assert runner["RunnerURL"] == health["url"]
    assert runner["App"] == deployment.app and runner["Status"] == "idle"
    assert runner["HealthURL"] == "http://readiness:8080/healthz"
    assert node["Network"] == "offchain"
    assert len(runner["Metadata"].encode()) <= 1024
    assert advertised["runtime"]["image"] == runtime["image"]
    assert "ports" not in runtime  # Runtime admin APIs are private to the compose network.
    assert all("@sha256:" in s["image"] for s in compose["services"].values())
    if family != "ollama":
        args = runtime["command"]
        assert args[args.index("--revision") + 1] == deployment.model.revision
        assert args[args.index("--served-model-name") + 1] == deployment.model.name
    if family == "vllm":
        assert "--enable-prompt-tokens-details" in runtime["command"]
        # vLLM runs no more sequences than the node admits.
        assert args[args.index("--max-num-seqs") + 1] == str(runner["Capacity"])
    if family == "sglang":
        assert "--enable-cache-report" in runtime["command"]
    assert advertised["endpoints"]["chat_completions"] == PROFILES["chat_completions"]


def test_supported_vllm_manifest_renders_benchmarked_capacity(tmp_path):
    path = BUNDLE / "runtimes" / "vllm" / "qwen3.8-27b-awq.json"
    deployment = Deployment.model_validate(json.loads(path.read_text()))
    render(deployment, tmp_path)
    runner = tomllib.loads((tmp_path / "runners.toml").read_text())["Runners"][0]
    args = json.loads((tmp_path / "compose.json").read_text())["services"]["runtime"]["command"]
    assert runner["Capacity"] == 8 and args[args.index("--max-num-seqs") + 1] == "8"
    assert deployment.model.revision == "e3a700906e6a73ba4290b1873dc2ba8198527ab5"


def test_changed_configuration_invalidates_deployment_identity(tmp_path):
    first = render(Deployment.model_validate(manifest()), tmp_path / "a")
    same = render(Deployment.model_validate(manifest()), tmp_path / "b")
    changed = manifest()
    changed["runtime"]["tool_parser"] = "hermes"
    second = render(Deployment.model_validate(changed), tmp_path / "c")
    assert first["deployment_revision"] == same["deployment_revision"]
    assert first["deployment_revision"] != second["deployment_revision"]


@pytest.mark.parametrize("family", ["vllm", "ollama", "sglang"])
def test_templates_require_real_model_pins(family):
    raw = (BUNDLE / "runtimes" / family / "manifest.example.json").read_text()
    with pytest.raises(ValidationError):
        Deployment.model_validate_json(raw)


@pytest.mark.parametrize(
    "patch",
    [
        {"runtime": {"name": "unknown"}},
        {"capacity": 0},
        {"capacity": True},
        {"admission": "verified"},
        {"public_url": "http://user:secret@host"},
        {"endpoints": ["chat_completions", "chat_completions"]},
        {"endpoints": ["admin"]},
        {"contract_version": 2},
        {"command": ["override-model"]},
    ],
)
def test_invalid_manifests_fail_before_output(tmp_path, patch):
    with pytest.raises(ValidationError):
        render(Deployment.model_validate({**manifest(), **patch}), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_published_schema_and_profiles_match_code():
    assert (
        json.loads((BUNDLE / "contract/deployment.schema.json").read_text())
        == Deployment.model_json_schema()
    )
    assert json.loads((BUNDLE / "contract/profiles.json").read_text()) == PROFILES


@pytest.mark.parametrize("family", ["vllm", "sglang"])
def test_readiness_checks_model_and_health(family):
    calls = []

    def fetch(url):
        calls.append(url)
        return {"data": [{"id": "expected"}]} if url.endswith("models") else {}

    config = {"runtime": family, "url": "http://runtime:8000", "model": "expected"}
    assert check_ready(config, fetch)
    assert calls == ["http://runtime:8000/v1/models", "http://runtime:8000/health"]
    assert not check_ready({**config, "model": "wrong"}, fetch)


@pytest.mark.parametrize(
    "loaded,digest,expected",
    [(False, "a" * 64, False), (True, "b" * 64, False), (True, "a" * 64, True)],
)
def test_ollama_requires_loaded_matching_digest(loaded, digest, expected):
    def fetch(url):
        if url.endswith("models"):
            return {"data": [{"id": "model:tag"}]}
        return {"models": [{"name": "model:tag", "digest": digest}] if loaded else []}

    config = {
        "runtime": "ollama",
        "url": "http://runtime:11434",
        "model": "model:tag",
        "model_revision": "sha256:" + "a" * 64,
    }
    assert check_ready(config, fetch) is expected


def test_native_endpoint_metadata_is_explicit(tmp_path):
    raw = manifest()
    raw["endpoints"] = ["chat_completions", "responses", "messages"]
    meta = render(Deployment.model_validate(raw), tmp_path)
    assert set(meta["endpoints"]) == set(raw["endpoints"])
    assert meta["admission"] == "candidate"  # Configuration is never certification.


def test_metadata_schema_matches_code():
    assert (
        json.loads((BUNDLE / "contract/metadata.schema.json").read_text())
        == RunnerMetadata.model_json_schema()
    )


@pytest.mark.parametrize(
    "change", [{"inference_contract": 2}, {"admission": "verified"}, {"model_name": " model "}]
)
def test_unknown_metadata_contracts_are_rejected(tmp_path, change):
    value = render(Deployment.model_validate(manifest()), tmp_path)
    with pytest.raises(ValidationError):
        RunnerMetadata.model_validate({**value, **change})


def test_endpoint_profile_cannot_supply_arbitrary_paths(tmp_path):
    value = render(Deployment.model_validate(manifest()), tmp_path)
    value["endpoints"]["chat_completions"]["path"] = "/admin/load-model"
    with pytest.raises(ValidationError, match="endpoint profile"):
        RunnerMetadata.model_validate(value)


def test_vision_probe_uses_deltas_without_repeating_completed_text():
    import runpy

    probe = runpy.run_path("live-runner/tests/probe_vision.py")
    assert (
        probe["stream_text"](
            "responses",
            [
                {"type": "response.output_text.delta", "delta": "r"},
                {"type": "response.output_text.delta", "delta": "ed"},
                {"type": "response.output_text.done", "text": "red"},
                {"type": "response.completed", "response": {"output": [{"text": "red"}]}},
            ],
        )
        == "red"
    )
    assert (
        probe["stream_text"](
            "chat_completions",
            [
                {"choices": [{"delta": {"content": "bl"}}]},
                {"choices": [{"delta": {"content": "ue"}}]},
                {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2}},
            ],
        )
        == "blue"
    )
    assert (
        probe["stream_text"](
            "messages",
            [
                {
                    "type": "content_block_delta",
                    "delta": {"type": "thinking_delta", "thinking": "ignore"},
                },
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "red"}},
            ],
        )
        == "red"
    )
