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


def render_onchain(tmp_path, extra=""):
    env = tmp_path / ".env"
    env.write_text("ORCHESTRATOR_ADDRESS=0x" + "1" * 40 + "\n" + extra)
    return subprocess.run(
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
        text=True,
    )


def test_default_tickets_are_worth_more_than_capped_redemption_gas(tmp_path):
    assert render_onchain(tmp_path).returncode == 0
    config = tomllib.loads((tmp_path / "generated/orchestrator.toml").read_text())
    face, fee_cap = int(config["TicketFaceValue"]), int(config["RedeemerMaxFeePerGas"])
    assert face == 400_000_000_000_000
    assert face >= fee_cap * 250_000
    # Expected value per ticket stays 4e11 wei: a one-in-1000 chance at the face value.
    assert int(config["TicketWinProb"]) == (2**256 - 1) * 400_000_000_000 // face


@pytest.mark.parametrize(
    "extra",
    [
        "TICKET_FACE_VALUE_WEI=400000000000\n",  # the upstream example: below gas
        "REDEEMER_MAX_FEE_PER_GAS_WEI=2000000000\n",  # ceiling raises worst-case gas above face
        "TICKET_EV_WEI=400000000000000\n",  # a certain win is not a lottery ticket
        "TICKET_EV_WEI=0\n",
    ],
)
def test_uneconomic_ticket_parameters_are_rejected(tmp_path, extra):
    result = render_onchain(tmp_path, extra)
    assert result.returncode != 0
    assert not (tmp_path / "generated/orchestrator.toml").exists()


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
