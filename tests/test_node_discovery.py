"""Read a livepeer/node discovery document through the gateway's own engine composition.

Builder Core v0.2.0 reads runners only from `LPB_DISCOVERY_URL` (its D63). The document below has
the shape of livepeer/node `f45d83f`'s `livepeer-discovery` output: a leading metadata entry with
no `address`, runner URLs on the orchestrator's origin, nanosecond stamps and exact price tokens.
No payment service is contacted: the engine is created but never started.
"""

import json
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from livepeer_builder import JobRequest, PriceInfo

from inference_gateway.adapters.runner_contract import PROFILES
from inference_gateway.backends.native_selection import Admission, NativeSelection
from inference_gateway.config import LivepeerSettings, ModelRoute
from inference_gateway.runtime import build_engine

STAMP = "2026-11-10T17:31:43.725543618Z"
APP = "inference/vllm"
REVISION = "sha256:" + "b" * 64


def contract(**changes):
    value = {
        "inference_contract": 1,
        "runner_id": "vision-runner",
        "model_name": "vision-model",
        "runtime": {"name": "vllm", "image": "vllm/vllm-openai@sha256:" + "a" * 64},
        "deployment_revision": REVISION,
        "endpoints": {"chat_completions": PROFILES["chat_completions"]},
        "limits": {"context_tokens": 4096},
        "admission": "candidate",
        **changes,
    }
    return json.dumps({k: v for k, v in value.items() if v is not None}, separators=(",", ":"))


PRICE_USD = "0.000019444444444444444444444444444444"  # more digits than a float keeps


def record(url, metadata, app=APP):
    return {
        "observed_at": STAMP,
        "url": url,
        "app": app,
        "metadata": metadata,
        "mode": "single-shot",
        "capacity": 1,
        "capacity_used": 0,
        "capacity_available": 1,
        "price_info": {
            "price": 9625355572,
            "price_usd": "PRICE_USD",
            "currency": "wei",
            "unit": "seconds",
        },
    }


def document(base):
    runners = [
        record(f"{base}/apps/vision-runner/app", contract()),
        # Contract metadata without a runner ID: never inferred from the URL path.
        record(f"{base}/apps/vision-runner-2/app", contract(runner_id=None)),
        # Claimed by this entry but served from another origin: Core drops orchestrator_url.
        record("https://elsewhere.example/apps/vision-runner/app", contract()),
        record(f"{base}/apps/other/app", contract(), app="other/app"),
    ]
    entries = [
        {"nodes": [{"url": base, "queried_at": STAMP}]},  # node's metadata entry: no address
        {"address": base, "runners": runners},
    ]
    # Write price_usd as node does: one exact JSON number token.
    return json.dumps(entries).replace('"PRICE_USD"', PRICE_USD).encode()


@pytest.fixture
def node_discovery():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/discovery":
                self.send_error(404)
                return
            body = document(f"http://127.0.0.1:{self.server.server_port}")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def engine_env(monkeypatch, tmp_path, node_discovery):
    from cryptography.fernet import Fernet

    for name, value in {
        "LPB_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path}/engine.db",
        "LPB_ALLOCATION_MODE": "per_application",
        "LPB_SIGNER_URL": "http://signer.test:8937",
        "LPB_BATTERIES_URL": "http://batteries.test:8081",
        "LPB_BATTERIES_TOKEN": "fixture-token",
        "LPB_SHARED_PAYMENT_KEY": "fixture-payment-key",
        "LPB_SHARED_ALLOCATION_ID": "fixture-allocation",
        "LPB_SECRET_KEY": Fernet.generate_key().decode(),
        "LPB_DISCOVERY_URL": f"{node_discovery}/discovery",
    }.items():
        monkeypatch.setenv(name, value)
    return node_discovery


class _PreserveOrder:
    def order(self, candidates, request):
        return candidates


@pytest.mark.parametrize("allowlisted", [True, False])
async def test_gateway_reads_node_discovery_and_admits_by_metadata_identity(
    engine_env, allowlisted
):
    base = engine_env
    settings = LivepeerSettings(
        routes=(ModelRoute(name="vision-model", app=APP, mode="native"),),
        approved_orchestrators=(base,) if allowlisted else (),
    )
    engine = await build_engine(settings)
    try:
        snapshot = await engine.discovery.refresh()
        assert snapshot.last_error is None
        found = engine.discovery.candidates(APP)
        urls = {runner.url for runner in found}
        assert f"{base}/apps/vision-runner/app" in urls
        assert f"{base}/apps/vision-runner-2/app" not in urls  # no declared identity
        assert engine.discovery.candidates("other/app") == []  # not a configured app
        # The allowlist compares orchestrator_url, which Core sets only on the runner's origin.
        assert ("https://elsewhere.example/apps/vision-runner/app" in urls) is not allowlisted
        listed = next(r for r in found if r.url == f"{base}/apps/vision-runner/app")
        assert listed.orchestrator_url == base
        assert listed.metadata == contract()
        assert listed.price_info == PriceInfo(
            price=Decimal("9625355572"),
            price_usd=Decimal(PRICE_USD),
            currency="wei",
            unit="seconds",
        )

        approval = Admission(base, "vision-runner", APP, "vision-model", REVISION,
                             "chat_completions", 2e10)  # fmt: skip
        policy = NativeSelection(_PreserveOrder(), lambda: (approval,))
        job = JobRequest(app=APP, model="vision-model", path="/v1/chat/completions")
        # Only the runner on the approved origin is admitted, never its off-origin namesake.
        assert [r.url for r in policy.order(found, job)] == [f"{base}/apps/vision-runner/app"]
    finally:
        await engine.stop()
