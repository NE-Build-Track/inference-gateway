"""Render explicit offchain/onchain node configuration; never fund or start services."""

import argparse
import json
import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit


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
    price = Decimal(values.get("RUNNER_PRICE_USD", "1"))
    unit = values.get("RUNNER_PRICE_UNIT", "hour")
    if capacity < 1 or not price.is_finite() or price < 0 or unit not in {"hour", "fixed"}:
        parser.error("Invalid runner capacity or wholesale price")
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
                'HealthGrants = ["vllm:8000"]',
            ]
        )
        + "\n"
    )
    if args.mode == "onchain":
        address = values.get("ORCHESTRATOR_ADDRESS", "")
        if len(address) != 42 or not address.startswith("0x"):
            parser.error("Set ORCHESTRATOR_ADDRESS for on-chain mode")
        int(address[2:], 16)
        config += (
            "\n".join(
                [
                    'Network = "arbitrum-one-mainnet"',
                    "OrchestratorAddress = " + json.dumps(address),
                    'KeystoreFile = "/run/secrets/keystore.json"',
                    'KeystorePasswordFile = "/run/secrets/keystore-password"',
                    'RPCURLFile = "/run/secrets/rpc-url"',
                    'ETHUSDFeed = "0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612"',
                    'TicketFaceValue = "400000000000"',
                    'TicketWinProb = "'
                    '57896044618658097711785492504343953926634992332820282019728792003956564819967"',
                ]
            )
            + "\n"
        )
    runners = (
        "\n".join(
            [
                "[[Runners]]",
                'ID = "vllm"',
                'RunnerURL = "http://vllm:8000"',
                "App = " + json.dumps(values.get("RUNNER_APP", "inference/chat/default")),
                'Mode = "single-shot"',
                'Status = "ready"',
                f"Capacity = {capacity}",
                'HealthURL = "http://vllm:8000/health"',
                "HealthCode = 200",
                "[Runners.PriceInfo]",
                "Price = " + json.dumps(str(price)),
                'Currency = "usd"',
                "Unit = " + json.dumps(unit),
            ]
        )
        + "\n"
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "orchestrator.toml").write_text(config)
    (args.output / "runners.toml").write_text(runners)
    print("Rendered " + args.mode + " configuration; no services started or funds moved.")


if __name__ == "__main__":
    main()
