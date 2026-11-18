"""Read a livepeer/node discovery document through the gateway's own engine composition.

Builder Core reads runners only from `LPB_DISCOVERY_URL` (its D63); it has no discovery process of
its own to run. The document below has the shape of livepeer/node's `livepeer-discovery` output: a
leading metadata entry with no `address`, runner URLs on the orchestrator's origin, nanosecond
stamps and exact price tokens. No payment service is contacted: the engine is created but never
started.
"""

import json
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from contract_helpers import approval, envelope, policy
from livepeer_builder import JobRequest, PriceInfo

from inference_gateway.backends.discovery import NATIVE, ContractSelection
from inference_gateway.config import LivepeerSettings, ModelRoute
from inference_gateway.runtime import build_engine

STAMP = "2026-11-10T17:31:43.725543618Z"
REVISION = "sha256:" + "b" * 64
PRICE_USD = "0.000019444444444444444444444444444444"  # more digits than a float keeps


def metadata(runner_id="vision-runner"):
    return envelope("vision-model", ("chat_completions",), revision=REVISION, runner_id=runner_id)


def record(url, runner_metadata, app="chat"):
    return {
        "observed_at": STAMP,
        "url": url,
        "app": app,
        "metadata": runner_metadata,
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
        record(f"{base}/apps/vision-runner/app", metadata()),
        # An envelope with no runner ID: one is never inferred from the URL path.
        record(f"{base}/apps/vision-runner-2/app", metadata(runner_id=None)),
        # Claimed by this entry but served from another origin: Core drops orchestrator_url.
        record("https://elsewhere.example/apps/vision-runner/app", metadata()),
        record(f"{base}/apps/other/app", metadata(), app="decision"),
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
async def test_gateway_reads_node_discovery_and_admits_by_envelope_identity(
    engine_env, allowlisted
):
    base = engine_env
    settings = LivepeerSettings(
        routes=(ModelRoute(name="vision-model", capability="chat", mode="native"),),
        approved_orchestrators=(base,) if allowlisted else (),
    )
    runner_policy = policy(approval(base, "vision-runner", "vision-model", revision=REVISION))
    engine = await build_engine(settings, runner_policy)
    try:
        snapshot = await engine.discovery.refresh()
        assert snapshot.last_error is None
        found = engine.discovery.candidates("chat")
        urls = {runner.url for runner in found}
        assert f"{base}/apps/vision-runner/app" in urls
        # A runner whose envelope names no ID is listed; it serves only where no approval is needed.
        assert f"{base}/apps/vision-runner-2/app" in urls
        assert engine.discovery.candidates("decision") == []  # not a route's capability
        # The allowlist compares orchestrator_url, which Core sets only on the runner's origin.
        assert ("https://elsewhere.example/apps/vision-runner/app" in urls) is not allowlisted
        listed = next(r for r in found if r.url == f"{base}/apps/vision-runner/app")
        assert listed.orchestrator_url == base
        assert listed.metadata == metadata()
        assert listed.price_info == PriceInfo(
            price=Decimal("9625355572"),
            price_usd=Decimal(PRICE_USD),
            currency="wei",
            unit="seconds",
        )

        job = JobRequest(
            app="chat", model="vision-model", path="/v1/chat/completions", capability=NATIVE
        )
        # A native job needs an approval: only the runner that names the approved ID on the
        # approved origin is admitted, never its off-origin namesake or its nameless neighbour.
        selection = ContractSelection(
            _PreserveOrder(), runner_policy.contract, runner_policy.approvals
        )
        admitted = selection.order(found, job)
        assert [r.url for r in admitted] == [f"{base}/apps/vision-runner/app"]
    finally:
        await engine.stop()
