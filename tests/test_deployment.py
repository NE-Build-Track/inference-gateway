"""Validate generated deployment contracts without a GPU or paid network calls."""

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mode", ["offchain", "onchain"])
def test_orchestrator_config_rendering(tmp_path, mode):
    env = tmp_path / ".env"
    env.write_text(
        "ORCHESTRATOR_SERVICE_URL=https://inference.example\n"
        "ORCHESTRATOR_ADDRESS=0x" + "1" * 40 + "\nRUNNER_PRICE_USD=1.25\n"
    )
    output = tmp_path / "generated"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "deploy/livepeer/orchestrator/render-config.py"),
            "--env-file",
            str(env),
            "--output",
            str(output),
            "--mode",
            mode,
        ],
        check=True,
    )
    config = tomllib.loads((output / "orchestrator.toml").read_text())
    runners = tomllib.loads((output / "runners.toml").read_text())
    assert config["ServiceURL"] == "https://inference.example"
    assert config["RunnerConfig"] == "/etc/livepeer/runners.toml"
    assert runners["Runners"][0]["RunnerURL"] == "http://vllm:8000"
    assert runners["Runners"][0]["PriceInfo"]["Price"] == "1.25"
    assert ("Network" in config) == (mode == "onchain")


def test_paid_config_requires_explicit_identity(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ORCHESTRATOR_ADDRESS=\n")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "deploy/livepeer/orchestrator/render-config.py"),
            "--env-file",
            str(env),
            "--output",
            str(tmp_path / "generated"),
            "--mode",
            "onchain",
        ],
        capture_output=True,
    )
    assert result.returncode != 0
    assert not (tmp_path / "generated/orchestrator.toml").exists()
