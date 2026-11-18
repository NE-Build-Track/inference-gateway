"""Offline acceptance for the runner contract v1 bundle, without Docker, GPU or network."""

import importlib.util
import json
import re
import shlex
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from contract_helpers import contract
from livepeer_runner_contract import Envelope, parse_envelope
from livepeer_runner_contract.cli import cmd_ready, parser
from livepeer_runner_contract.manifest import parse_manifest, revision_of
from pydantic import ValidationError

from inference_gateway.backends.runner_contract import ENDPOINTS

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "live-runner"


def module(name):
    spec = importlib.util.spec_from_file_location(name, BUNDLE / "tooling" / f"{name}.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


renderer = module("render")
Deployment, DIGEST = renderer.Deployment, renderer.DIGEST
PRICE = {"amount": 0.5, "unit": "hour"}


def render(deployment, output):
    return renderer.render(deployment, output, contract().registry)


def manifest(family="vllm"):
    value = json.loads((BUNDLE / "runtimes" / family / "manifest.example.json").read_text())
    value["model"]["revision"] = "sha256:" + "a" * 64 if family == "ollama" else "a" * 40
    value["price"] = PRICE
    return value


def blocks(path):
    return tomllib.loads((path / "runners.toml").read_text())["Runners"]


@pytest.mark.parametrize("family", ["vllm", "ollama", "sglang"])
def test_bundle_identity_config_and_health_are_aligned(tmp_path, family):
    deployment = Deployment.model_validate(manifest(family))
    [(ident, advertised)] = render(deployment, tmp_path).items()
    compose = json.loads((tmp_path / "compose.json").read_text())
    [runner] = blocks(tmp_path)
    node = tomllib.loads((tmp_path / "orchestrator.toml").read_text())
    runtime = compose["services"]["runtime"]
    assert json.loads(runner["Metadata"]) == advertised
    assert isinstance(parse_envelope(runner["Metadata"]), Envelope)
    assert advertised["runner_contract"] == 1
    assert advertised["runner_id"] == runner["ID"] == ident == deployment.runner_id
    assert advertised["model"]["name"] == deployment.model.name
    origin = "ollama" if family == "ollama" else "hf"
    assert advertised["model"]["source"] == f"{origin}:{deployment.model.source}"
    assert runner["RunnerURL"] == f"http://runtime:{11434 if family == 'ollama' else 8000}"
    # An App ID is a capability and nothing else.
    assert runner["App"] == "chat" and runner["Mode"] == "single-shot"
    assert runner["Status"] == "ready"
    assert runner["HealthURL"] == "http://readiness-chat:8080/healthz"
    assert node["HealthGrants"] == ["readiness-chat:8080"]
    assert runner["PriceInfo"] == {"Price": 0.5, "Currency": "usd", "Unit": "hour"}
    assert node["Network"] == "offchain"
    assert len(runner["Metadata"].encode()) <= 1024
    assert runtime["image"].endswith("@" + advertised["runtime"]["image"])
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
    assert advertised["endpoints"] == {"chat_completions": {"profile": "openai.chat.v2"}}


@pytest.mark.parametrize("family", ["vllm", "ollama", "sglang"])
def test_the_standard_tool_recomputes_each_revision_from_the_written_manifest(tmp_path, family):
    rendered = render(Deployment.model_validate(manifest(family)), tmp_path)
    for ident, envelope in rendered.items():
        text = (tmp_path / f"{ident}.runner.toml").read_text()
        parsed = parse_manifest(text, capabilities=contract().registry.capabilities)
        assert revision_of(parsed) == envelope["deployment_revision"]
        block = tomllib.loads((tmp_path / f"{ident}.block.toml").read_text())["Runners"]
        assert [json.loads(b["Metadata"]) for b in block] == [envelope]
    release = json.loads((tmp_path / "release.json").read_text())
    assert release["registrations"] == {i: e["deployment_revision"] for i, e in rendered.items()}
    assert release["registry_release"] == "2026.11.3"


def test_supported_vllm_manifest_renders_benchmarked_capacity(tmp_path):
    path = BUNDLE / "runtimes" / "vllm" / "qwen3.8-27b-awq.json"
    raw = json.loads(path.read_text())
    with pytest.raises(ValidationError):
        Deployment.model_validate(raw)  # Its price is the operator's to set.
    deployment = Deployment.model_validate({**raw, "price": PRICE})
    render(deployment, tmp_path)
    [runner] = blocks(tmp_path)
    args = json.loads((tmp_path / "compose.json").read_text())["services"]["runtime"]["command"]
    assert runner["Capacity"] == 8 and args[args.index("--max-num-seqs") + 1] == "8"
    assert deployment.model.revision == "e3a700906e6a73ba4290b1873dc2ba8198527ab5"
    # Qwen3.8 thinks unless asked not to, and the parser keeps its thinking out of the answer.
    assert json.loads(runner["Metadata"])["reasoning"] == {"default": "on"}


def test_a_model_that_reasons_needs_a_reasoning_parser(tmp_path):
    with pytest.raises(ValidationError, match="reasoning parser"):
        Deployment.model_validate({**manifest(), "reasoning": "on"})
    parsed = manifest()
    parsed["runtime"]["reasoning_parser"] = "qwen3"
    [envelope] = render(Deployment.model_validate({**parsed, "reasoning": "on"}), tmp_path).values()
    assert envelope["reasoning"] == {"default": "on"}
    [undeclared] = render(Deployment.model_validate(manifest()), tmp_path / "plain").values()
    assert "reasoning" not in undeclared


def revision(value, tmp_path, name):
    [envelope] = render(Deployment.model_validate(value), tmp_path / name).values()
    return envelope["deployment_revision"]


def test_behavior_changes_change_the_revision_and_operations_do_not(tmp_path):
    base = revision(manifest(), tmp_path, "a")
    assert revision(manifest(), tmp_path, "b") == base
    parser = manifest()
    parser["runtime"]["tool_parser"] = "hermes"
    model = manifest()
    model["model"]["revision"] = "b" * 40
    context = manifest()
    context["model"]["context_tokens"] = 8192
    for name, changed in {"parser": parser, "model": model, "context": context}.items():
        assert revision(changed, tmp_path, name) != base
    # Moving the node or raising capacity keeps the revision, and so keeps approvals.
    moved = {
        **manifest(),
        "capacity": 4,
        "public_url": "https://elsewhere:8935",
        "price": {"amount": 2.0, "unit": "fixed"},
    }
    assert revision(moved, tmp_path, "moved") == base


def test_a_deployment_serving_chat_and_decisions_registers_once_per_capability(tmp_path):
    raw = manifest("sglang")
    raw["endpoints"] = list(ENDPOINTS)
    rendered = render(Deployment.model_validate(raw), tmp_path)
    by_app = {block["App"]: block for block in blocks(tmp_path)}
    assert set(by_app) == {"chat", "decision"}
    assert (
        by_app["chat"]["ID"] == "sglang-llm-chat"
        and by_app["decision"]["ID"] == "sglang-llm-decision"
    )
    chat, decision = rendered["sglang-llm-chat"], rendered["sglang-llm-decision"]
    assert set(chat["endpoints"]) == {"chat_completions", "responses", "messages"}
    assert set(decision["endpoints"]) == {"systemone"}
    # One engine behind both: the group says so; each revision covers its own profiles.
    assert chat["capacity_group"] == decision["capacity_group"] == {"id": "sglang-llm", "slots": 1}
    assert chat["deployment_revision"] != decision["deployment_revision"]
    # Each registration's health check self-tests its own profiles.
    assert by_app["chat"]["HealthURL"] == "http://readiness-chat:8080/healthz"
    assert by_app["decision"]["HealthURL"] == "http://readiness-decision:8080/healthz"
    node = tomllib.loads((tmp_path / "orchestrator.toml").read_text())
    assert node["HealthGrants"] == ["readiness-chat:8080", "readiness-decision:8080"]


@pytest.mark.parametrize("family", ["vllm", "ollama", "sglang"])
def test_templates_require_real_model_pins_and_a_price(family):
    raw = json.loads((BUNDLE / "runtimes" / family / "manifest.example.json").read_text())
    with pytest.raises(ValidationError):
        Deployment.model_validate(raw)
    with pytest.raises(ValidationError):
        Deployment.model_validate({**raw, "price": PRICE})
    pinned = manifest(family)
    pinned["price"] = raw["price"]
    with pytest.raises(ValidationError):
        Deployment.model_validate(pinned)


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
        {"price": {"amount": 0, "unit": "hour"}},
        {"price": {"amount": 1, "unit": "second"}},
        # Before v1, a manifest named an App ID and a contract version.
        {"app": "inference/vllm"},
        {"contract_version": 1},
        {"command": ["override-model"]},
    ],
)
def test_invalid_manifests_fail_before_output(tmp_path, patch):
    with pytest.raises(ValidationError):
        render(Deployment.model_validate({**manifest(), **patch}), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_private_admission_reaches_the_envelope(tmp_path):
    [envelope] = render(
        Deployment.model_validate({**manifest(), "admission": "private"}), tmp_path
    ).values()
    assert envelope["admission"] == "private"
    [envelope] = render(Deployment.model_validate(manifest()), tmp_path / "default").values()
    assert (
        "admission" not in envelope
    )  # candidate is the default; configuration isn't certification


def test_published_schema_matches_code():
    assert (
        json.loads((BUNDLE / "contract/deployment.schema.json").read_text())
        == Deployment.model_json_schema()
    )


READINESS_IMAGE = json.loads((BUNDLE / "images.lock.json").read_text())["images"]["readiness"]


def ready_command(service):
    """The `runner-contract ready` arguments a readiness service runs once the tool installs."""
    shell, flag, script = service["command"]
    install, ready = script.split(" && exec ")
    assert (shell, flag) == ("sh", "-c") and install == renderer.READINESS_INSTALL
    words = shlex.split(ready)
    assert words[:3] == ["python", "-m", "livepeer_runner_contract"]
    return words[3:]


@pytest.mark.parametrize("family", ["vllm", "ollama", "sglang"])
def test_each_registration_is_ready_by_the_standards_check(tmp_path, family):
    raw = manifest(family)
    raw["endpoints"] = list(ENDPOINTS)
    render(Deployment.model_validate(raw), tmp_path)
    services = json.loads((tmp_path / "compose.json").read_text())["services"]
    engine = "http://runtime:11434/" if family == "ollama" else "http://runtime:8000/health"
    for block in blocks(tmp_path):
        host = urlsplit(block["HealthURL"]).hostname
        service = services[host]
        args = parser().parse_args(ready_command(service))
        assert args.run is cmd_ready
        assert (args.manifest, args.engine_health, args.listen) == (
            "/app/runner.toml",
            engine,
            "0.0.0.0:8080",
        )
        # It reads the manifest this registration was rendered from, so it self-tests exactly
        # the profiles the block advertises, against the engine the block names.
        mounted = f"./{block['ID']}.runner.toml:/app/runner.toml:ro"
        assert mounted in service["volumes"]
        assert "./readiness.txt:/app/readiness.txt:ro" in service["volumes"]
        text = (tmp_path / f"{block['ID']}.runner.toml").read_text()
        parsed = parse_manifest(text, capabilities=contract().registry.capabilities)
        assert parsed.health_url == block["HealthURL"] and parsed.url == block["RunnerURL"]
        assert service["read_only"] and service["image"] == READINESS_IMAGE
    assert services["orchestrator"]["depends_on"] == sorted(
        urlsplit(b["HealthURL"]).hostname for b in blocks(tmp_path)
    )
    assert (tmp_path / "readiness.txt").read_bytes() == renderer.READINESS_REQUIREMENTS.read_bytes()


def requirement_lines(path):
    return [
        line for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")
    ]


def test_readiness_installs_the_library_the_gateway_pins():
    """The readiness check runs the same standard, at the same commits and dependency versions,
    as the gateway that reads the envelopes."""
    pinned = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    lock = {p["name"]: p for p in tomllib.loads((ROOT / "uv.lock").read_text())["package"]}
    wanted, exact = {}, {}
    for line in requirement_lines(renderer.READINESS_REQUIREMENTS):
        if " @ " in line:
            name, url = line.split(" @ ")
            match = re.fullmatch(
                r"https://github\.com/NE-Build-Track/live-runner-standard/archive/([0-9a-f]{40})"
                r"\.tar\.gz#subdirectory=(python/[a-z-]+)",
                url,
            )
            assert match, line
            wanted[name] = match.groups()
        else:
            name, version = line.split("==")
            exact[name] = version
    for name, (commit, subdirectory) in wanted.items():
        [gateway] = [d for d in pinned if d.startswith(name + " @ ")]
        assert gateway.endswith(f"@{commit}#subdirectory={subdirectory}"), (name, gateway)
    # Every dependency of the two packages, at the version the gateway was tested with.
    needed, queue = set(), list(wanted)
    while queue:
        for dependency in lock[queue.pop()].get("dependencies", []):
            if dependency["name"] not in needed:
                needed.add(dependency["name"])
                queue.append(dependency["name"])
    assert set(exact) == needed
    assert all(lock[name]["version"] == version for name, version in exact.items())


CLEF = BUNDLE / "runtimes/clef"


def clef_images():
    """Each service's image from the Compose file; no YAML library is a project dependency."""
    found, service = {}, None
    for line in (CLEF / "compose.yaml").read_text().splitlines():
        if match := re.fullmatch(r"  ([a-z-]+):", line):
            service = match.group(1)
        elif match := re.fullmatch(r"    image: (\S+)", line):
            found[service] = match.group(1)
    return found


def test_clef_configs_are_pinned_by_digest():
    images = clef_images()
    assert {"sglang", "vllm", "ollama", "llamacpp"} <= set(images)
    assert all(re.fullmatch(DIGEST, image) for image in images.values())


def test_clef_envelope_examples_are_v1_and_match_their_images():
    examples = json.loads((CLEF / "metadata.examples.json").read_text())
    images = clef_images()
    assert set(examples) == {"sglang", "vllm", "ollama", "llamacpp"}
    for backend, value in examples.items():
        raw = json.dumps(value, separators=(",", ":"))
        parsed = parse_envelope(raw)
        assert isinstance(parsed, Envelope) and parsed.size <= 1024
        assert images[backend].endswith("@" + parsed.runtime.image)
        # System One's profile serves `decision`, so these register with that App ID only.
        assert set(parsed.endpoints) == {"systemone"}
        assert contract().registry.entry(parsed.endpoints["systemone"].profile).capabilities == (
            "decision",
        )


@pytest.mark.parametrize("family", ["vllm", "ollama", "sglang"])
def test_system_one_is_not_limited_to_one_runtime(family):
    Deployment.model_validate({**manifest(family), "endpoints": ["systemone"]})


def test_llamacpp_has_an_example_but_no_rendered_command_line():
    raw = manifest("sglang")
    raw["runtime"]["name"] = "llamacpp"
    with pytest.raises(ValidationError):
        Deployment.model_validate(raw)


def test_every_endpoint_fits_the_metadata_limit(tmp_path):
    raw = manifest("sglang")
    raw["endpoints"] = list(ENDPOINTS)
    for block in blocks(tmp_path) if render(Deployment.model_validate(raw), tmp_path) else ():
        assert len(block["Metadata"].encode()) <= 1024


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


@pytest.mark.parametrize("escape", ["../escaped", "/tmp/escaped-bundle", "inside/../../escaped"])
def test_the_cli_refuses_an_output_outside_the_working_directory(tmp_path, monkeypatch, escape):
    work = tmp_path / "work"
    work.mkdir()
    (work / "manifest.json").write_text(json.dumps(manifest()))
    monkeypatch.chdir(work)
    monkeypatch.setattr("sys.argv", ["render.py", "manifest.json", "--output", escape])
    with pytest.raises(SystemExit, match="inside the working directory"):
        module("render").main()
    assert not (tmp_path / "escaped").exists() and not Path("/tmp/escaped-bundle").exists()
    monkeypatch.setattr("sys.argv", ["render.py", "manifest.json", "--output", "bundle"])
    module("render").main()
    assert (work / "bundle" / "compose.json").exists()
