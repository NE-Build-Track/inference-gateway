"""Smoke the built gateway image with FakeBackend and no network or payment configuration.

Usage: smoke-image.py [IMAGE], defaulting to inference-gateway:local.
"""

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4


def main() -> None:
    image = sys.argv[1] if len(sys.argv) > 1 else "inference-gateway:local"
    name = "gateway-smoke-" + uuid4().hex[:12]
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                name,
                "--env",
                "GATEWAY_BACKEND=fake",
                "--publish",
                "127.0.0.1::8000",
                "--read-only",
                "--tmpfs",
                "/tmp",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                image,
            ],
            check=True,
            capture_output=True,
        )
        address = subprocess.check_output(["docker", "port", name, "8000/tcp"], text=True).strip()
        base = "http://" + address
        for attempt in range(100):
            try:
                with urllib.request.urlopen(base + "/readyz", timeout=1) as result:
                    assert result.status == 200
                break
            except OSError:
                if attempt == 99:
                    raise
                time.sleep(0.1)
        for route in ["/v1/chat/completions", "/v1/messages"]:
            for streaming in [False, True]:
                body = {
                    "model": "fake-model",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": streaming,
                }
                if route == "/v1/messages":
                    body["max_tokens"] = 128
                request = urllib.request.Request(
                    base + route,
                    json.dumps(body).encode(),
                    headers={"Content-Type": "application/json", "anthropic-version": "2023-06-01"},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    raw = response.read().decode()
                    assert response.status == 200
                    if streaming:
                        assert "[DONE]" in raw or "message_stop" in raw
                    else:
                        assert "Echo: hello" in raw
        # Each SDK gets its own list format; the fake has no decision models to list or call.
        for headers, key, expected in [
            ({}, "data", ["fake-model"]),
            ({"anthropic-version": "2023-06-01"}, "data", ["fake-model"]),
            ({"x-typesafe-sdk": "smoke"}, "models", []),
        ]:
            request = urllib.request.Request(base + "/v1/models", headers=headers)
            with urllib.request.urlopen(request, timeout=5) as response:
                rows = json.load(response)[key]
                assert [row.get("id", row.get("name")) for row in rows] == expected
        request = urllib.request.Request(
            base + "/v1/systemone",
            json.dumps({"model": "fake-model", "state": "s", "questions": {}}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(request, timeout=5)
            raise AssertionError("The fake backend must reject System One")
        except urllib.error.HTTPError as error:
            assert error.code == 400
        print(f"Container smoke passed for {image}: readiness, model lists and all protocols")
    finally:
        subprocess.run(
            ["docker", "rm", "--force", "--volumes", name], check=False, capture_output=True
        )


if __name__ == "__main__":
    main()
