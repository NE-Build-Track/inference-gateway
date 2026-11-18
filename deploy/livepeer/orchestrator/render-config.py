"""Render explicit offchain/onchain node configuration; never fund or start services.

The vLLM runner registers under runner contract v1: App ID `chat`, and an envelope in `Metadata`.
Only the standard library is used, so the script runs with any Python 3.12.
"""

import argparse
import hashlib
import json
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

# Gas allowance for one winning-ticket redemption; a measured Arbitrum redemption used 191,870.
REDEMPTION_GAS = 250_000
# vllm/vllm-openai:v0.11.0 and python:3.12-slim-bookworm, pinned by digest.
VLLM_IMAGE = "vllm/vllm-openai@sha256:014a95f21c9edf6abe0aea6b07353f96baa4ec291c427bb1176dc7c93a85845c"
READINESS_IMAGE = "python@sha256:6dd8b6dab6b3adc7812bab69eca5dbdc1a5cc690ad28306d234ffb4ef8ac9753"
MODEL_ID = "Qwen/Qwen2.5-0.5B-Instruct"
MODEL_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
DIGEST = re.compile(r"^[a-zA-Z0-9./:_-]+@sha256:[0-9a-f]{64}$")
# What the readiness service installs when it starts: the standard's `runner-contract ready`.
READINESS = Path(__file__).resolve().parents[3] / "live-runner/tooling/readiness.txt"
STANDARD = "https://github.com/NE-Build-Track/live-runner-standard"


def deployment_revision(images, profiles, settings, model):
    """The standard's recipe (its SPEC.md): what changes behavior, and nothing else."""
    obj = {"images": sorted(images), "profiles": sorted(profiles), "settings": settings}
    obj["model"] = {"source": model["source"], "revision": model.get("revision")}
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["offchain", "onchain"], default="offchain")
    parser.add_argument("--env-file", type=Path, default=Path(__file__).with_name(".env"))
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "config/generated")
    args = parser.parse_args()
    values = {}
    for line in args.env_file.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    values.update(os.environ)
    url = values.get("ORCHESTRATOR_SERVICE_URL", "http://127.0.0.1:8935")
    if urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).hostname:
        parser.error("ORCHESTRATOR_SERVICE_URL must be an HTTP(S) URL")
    capacity = int(values.get("RUNNER_CAPACITY", "2"))
    try:
        price = Decimal(values.get("RUNNER_PRICE_USD", "1"))
    except InvalidOperation:
        parser.error("RUNNER_PRICE_USD must be a number")
    unit = values.get("RUNNER_PRICE_UNIT", "hour")
    if capacity < 1 or not price.is_finite() or price <= 0 or unit not in {"hour", "fixed"}:
        parser.error("Invalid runner capacity or wholesale price")
    image = values.get("VLLM_IMAGE") or VLLM_IMAGE
    readiness_image = values.get("READINESS_IMAGE") or READINESS_IMAGE
    if not DIGEST.match(image) or not DIGEST.match(readiness_image):
        parser.error("VLLM_IMAGE and READINESS_IMAGE must be pinned by digest (name@sha256:...)")
    revision = values.get("MODEL_REVISION") or MODEL_REVISION
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        parser.error("MODEL_REVISION must be the model's 40-character Hugging Face commit")
    served = values.get("SERVED_MODEL_NAME") or "gateway-model"
    parser_name = values.get("TOOL_CALL_PARSER") or "hermes"
    model = {"name": served, "source": "hf:" + (values.get("MODEL_ID") or MODEL_ID)}
    model["revision"] = revision
    envelope = {
        "runner_contract": 1,
        "deployment_revision": deployment_revision(
            [image, readiness_image],
            ["openai.chat.v2"],
            {"runtime": "vllm", "tool_parser": parser_name},
            model,
        ),
        "model": model,
        "endpoints": {"chat_completions": {"profile": "openai.chat.v2"}},
        "runtime": {"name": "vllm", "image": image.split("@", 1)[1]},
        # This node doesn't report runner IDs in discovery, so the envelope names it.
        "runner_id": "vllm",
    }
    metadata = json.dumps(envelope, separators=(",", ":"), ensure_ascii=False)
    if len(metadata.encode()) > 1024:
        parser.error(f"The envelope exceeds 1,024 bytes; see {STANDARD}")
    config = (
        "\n".join(
            [
                'DataDir = "/data"',
                'Listen = "0.0.0.0:8935"',
                'MetricsListen = "127.0.0.1:8936"',
                "ServiceURL = " + json.dumps(url),
                'RunnerServiceURL = "http://orchestrator:8935"',
                'RunnerConfig = "/etc/livepeer/runners.toml"',
                'RunnerGrants = ["vllm"]',
                'HealthGrants = ["readiness:8080"]',
            ]
        )
        + "\n"
    )
    if args.mode == "onchain":
        address = values.get("ORCHESTRATOR_ADDRESS", "")
        if len(address) != 42 or not address.startswith("0x"):
            parser.error("Set ORCHESTRATOR_ADDRESS for on-chain mode")
        int(address[2:], 16)
        face = int(values.get("TICKET_FACE_VALUE_WEI", "400000000000000"))
        ev = int(values.get("TICKET_EV_WEI", "20000000000"))
        fee_cap = int(values.get("REDEEMER_MAX_FEE_PER_GAS_WEI", "1000000000"))
        # The fee cap bounds redemption cost, so a ticket is never worth less than its gas.
        if fee_cap < 1 or face < fee_cap * REDEMPTION_GAS:
            parser.error("TICKET_FACE_VALUE_WEI must cover REDEEMER_MAX_FEE_PER_GAS_WEI x 250000")
        if not 0 < ev < face:
            parser.error("TICKET_EV_WEI must be positive and below TICKET_FACE_VALUE_WEI")
        win_prob = (2**256 - 1) * ev // face
        config += (
            "\n".join(
                [
                    'Network = "arbitrum-one-mainnet"',
                    "OrchestratorAddress = " + json.dumps(address),
                    'KeystoreFile = "/run/secrets/keystore.json"',
                    'KeystorePasswordFile = "/run/secrets/keystore-password"',
                    'RPCURLFile = "/run/secrets/rpc-url"',
                    'ETHUSDFeed = "0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612"',
                    f'TicketFaceValue = "{face}"',
                    f'TicketWinProb = "{win_prob}"',
                    f'RedeemerMaxFeePerGas = "{fee_cap}"',
                ]
            )
            + "\n"
        )
    runners = (
        "\n".join(
            [
                "[[Runners]]",
                'ID = "vllm"',
                # An App ID is a capability from the standard's list and nothing else.
                'App = "chat"',
                'Mode = "single-shot"',
                f"Capacity = {capacity}",
                'RunnerURL = "http://vllm:8000"',
                # Ready only after a self-test of the advertised profile passes.
                'HealthURL = "http://readiness:8080/healthz"',
                "HealthCode = 200",
                'Status = "ready"',
                # The gateway selects by the envelope's model name and profile.
                "Metadata = " + json.dumps(metadata),
                "[Runners.PriceInfo]",
                # A TOML number: the node reads Price as a json.Number.
                "Price = " + (str(price) if "." in str(price) else str(price) + ".0"),
                'Currency = "usd"',
                "Unit = " + json.dumps(unit),
            ]
        )
        + "\n"
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "orchestrator.toml").write_text(config)
    (args.output / "runners.toml").write_text(runners)
    (args.output / "readiness.txt").write_bytes(READINESS.read_bytes())
    print("Rendered " + args.mode + " configuration; no services started or funds moved.")


if __name__ == "__main__":
    main()
