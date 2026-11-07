"""Offline acceptance for the runner contract v1 bundle, without Docker, GPU or network."""

import importlib.util
import json
import re
import tomllib
from pathlib import Path

import pytest
from contract_helpers import contract
from livepeer_runner_contract import Envelope, parse_envelope
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
readiness = module("readiness")
check_ready = readiness.check
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
    health = json.loads((tmp_path / "readiness.json").read_text())
    runtime = compose["services"]["runtime"]
    assert json.loads(runner["Metadata"]) == advertised
    assert isinstance(parse_envelope(runner["Metadata"]), Envelope)
    assert advertised["runner_contract"] == 1
    assert advertised["runner_id"] == runner["ID"] == ident == deployment.runner_id
    assert advertised["model"]["name"] == health["model"] == deployment.model.name
    origin = "ollama" if family == "ollama" else "hf"
    assert advertised["model"]["source"] == f"{origin}:{deployment.model.source}"
    assert runner["RunnerURL"] == health["url"]
    # An App ID is a capability and nothing else.
    assert runner["App"] == "chat" and runner["Mode"] == "single-shot"
    assert runner["Status"] == "ready" and runner["HealthURL"] == "http://readiness:8080/healthz"
    assert runner["PriceInfo"] == {"Price": 0.5, "Currency": "usd", "Unit": "hour"}
    assert node["Network"] == "offchain"
    assert len(runner["Metadata"].encode()) <= 1024
    assert runtime["image"].endswith("@" + advertised["runtime"]["image"])
    assert "ports" not in runtime  # Runtime admin APIs are private to the compose network.
    assert all("@sha256:" in s["image"] for s in compose["services"].values())
    assert health["endpoints"] == [{"name": "chat_completions", "path": "/v1/chat/completions"}]
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
    health = json.loads((tmp_path / "readiness.json").read_text())
    assert [e["name"] for e in health["endpoints"]] == list(ENDPOINTS)


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


SERVED = {
    "/v1/chat/completions": {"choices": [{"index": 0}], "usage": {}},
    "/v1/responses": {"status": "completed", "usage": {}},
    "/v1/messages": {"type": "message", "usage": {}},
    "/v1/systemone": {"answers": {"sky": {"type": "noul", "noul": 0.9}}, "usage": {}},
}
HEALTH_CONFIG = {
    "runtime": "vllm",
    "url": "http://runtime:8000",
    "model": "m",
    "endpoints": [{"name": e, "path": p} for e, p in zip(ENDPOINTS, SERVED, strict=True)],
}


def visible(url):
    return {"data": [{"id": "m"}]} if url.endswith("models") else {}


def test_readiness_self_tests_every_profile_before_answering_ready():
    sent = []

    def post(url, body):
        sent.append((url, body["model"]))
        return 200, SERVED[url.removeprefix("http://runtime:8000")]

    state = readiness.Readiness(HEALTH_CONFIG, visible, post)
    assert readiness.answer(state.state) == (503, b'{"status": "starting"}')
    assert state.step() == "ready"
    assert [url for url, _ in sent] == [f"http://runtime:8000{p}" for p in SERVED]
    assert {model for _, model in sent} == {"m"}
    assert readiness.answer("ready") == (200, b'{"status": "ready"}')
    state.step()
    assert len(sent) == 4  # Ready stays ready without testing again while the model is listed.


@pytest.mark.parametrize(
    "broken",
    [
        (500, None),
        (200, {"choices": [{"index": 0}]}),  # no work report
        (200, {"error": {"message": "unsupported"}, "usage": {}}),
    ],
)
def test_a_failed_self_test_answers_failed_and_retries(broken):
    def post(url, body):
        if url.endswith("/v1/messages"):
            return broken
        return 200, SERVED[url.removeprefix("http://runtime:8000")]

    state = readiness.Readiness(HEALTH_CONFIG, visible, post)
    assert state.step() == "failed" and readiness.answer("failed")[0] == 503
    state.post = lambda url, body: (200, SERVED[url.removeprefix("http://runtime:8000")])
    assert state.step() == "ready"


def test_losing_the_model_returns_health_to_503_and_tests_again():
    tests = []

    def post(url, body):
        tests.append(url)
        return 200, SERVED[url.removeprefix("http://runtime:8000")]

    listed = {"value": False}
    state = readiness.Readiness(
        HEALTH_CONFIG, lambda url: visible(url) if listed["value"] else {"data": []}, post
    )
    assert state.step() == "starting" and not tests  # loading: never a self-test yet
    listed["value"] = True
    assert state.step() == "ready"
    listed["value"] = False
    assert state.step() == "failed"
    listed["value"] = True
    assert state.step() == "ready" and len(tests) == 8


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
