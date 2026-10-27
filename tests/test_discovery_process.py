"""Exercise the pinned discovery CLI against a local orchestrator, without payment services."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest


@pytest.mark.parametrize("metadata_identity", [False, True])
def test_discovery_process_preserves_native_runner_identity(tmp_path, metadata_identity):
    metadata = json.dumps(
        {
            "model_name": "vision-model",
            "deployment_revision": "sha256:" + "a" * 64,
            "runner_id": "vision-runner",
        }
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(
                [
                    {
                        "address": f"http://127.0.0.1:{self.server.server_port}",
                        "runners": [
                            {
                                **({} if metadata_identity else {"runner_id": "vision-runner"}),
                                "url": "https://vision-runner.example",
                                "app": "inference/vllm",
                                "mode": "single-shot",
                                "capacity": 1,
                                "capacity_available": 1,
                                "observed_at": datetime.now(UTC).isoformat(),
                                "metadata": metadata,
                            }
                        ],
                    }
                ]
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    source = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=source.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origins = tmp_path / "orchestrators.txt"
    origins.write_text(f"http://127.0.0.1:{source.server_port}\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("LPD_")}
    env.update(
        LPD_ORCHESTRATORS=str(origins),
        LPD_HTTP_HOST="127.0.0.1",
        LPD_HTTP_PORT=str(port),
        LPD_PROBE_INTERVAL_S="0.1",
        LPD_LOG_JSON="false",
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "livepeer_builder.cli", "discovery"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=1) as client:
            for _ in range(100):
                try:
                    response = client.get("/discovery")
                    if response.status_code == 200 and response.json():
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.05)
            else:
                raise AssertionError("Discovery service did not publish the local runner")
            runners = [runner for row in response.json() for runner in row.get("runners", [])]
            assert len(runners) == 1
            assert runners[0].get("runner_id") == (None if metadata_identity else "vision-runner")
            assert runners[0]["metadata"] == metadata
            assert runners[0]["url"] == "https://vision-runner.example"
            assert client.get("/readyz").status_code == 200
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        source.shutdown()
        source.server_close()
        thread.join(timeout=2)
