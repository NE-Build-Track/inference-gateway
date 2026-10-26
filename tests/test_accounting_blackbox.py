"""Provision only through CLI, exercise a separate server only through public HTTP."""

import json
import os
import socket
import subprocess
import sys
import time

import httpx


def test_cli_and_http_acceptance(tmp_path):
    db = str(tmp_path / "gateway.db")

    def cli(*args):
        result = subprocess.run(
            [sys.executable, "-m", "endpoint_gateway.cli", "--db", db, *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    alice = cli("users", "create", "Alice")["user_id"]
    bob = cli("users", "create", "Bob")["user_id"]
    operator = cli("users", "create", "Operator")["user_id"]
    credentials = {}
    key_ids = {}
    for label, user, scopes in [
        ("A", alice, "inference,reports"),
        ("A2", alice, "inference,reports"),
        ("B", bob, "inference,reports"),
        ("ADMIN", operator, "admin"),
    ]:
        issued = cli("keys", "create", user, "--scopes", scopes)
        credentials[label] = issued["secret"]
        key_ids[label] = issued["key_id"]
    cli(
        "prices",
        "set",
        "--model",
        "fake-model",
        "--version",
        "v1",
        "--input-per-million",
        "1",
        "--output-per-million",
        "2",
        "--cached-input-per-million",
        "0.1",
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "GATEWAY_ACCOUNTING_DB": db, "GATEWAY_BACKEND": "fake"}
    env.pop("GATEWAY_API_KEY", None)
    server = subprocess.Popen(
        [sys.executable, "-m", "endpoint_gateway", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=url, timeout=2) as http:
            for _ in range(100):
                try:
                    if http.get("/readyz").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.05)
            else:
                raise AssertionError("Server failed readiness")
            suite_env = {
                **env,
                "GATEWAY_TEST_URL": url,
                "GATEWAY_MODEL_NAME": "fake-model",
                "GATEWAY_TEST_INPUT_RATE": "1",
                "GATEWAY_TEST_OUTPUT_RATE": "2",
                "GATEWAY_TEST_CACHED_RATE": "0.1",
                "GATEWAY_TEST_RECONCILE_TIMEOUT": "15",
                **{"GATEWAY_TEST_KEY_" + k: v for k, v in credentials.items()},
            }
            result = subprocess.run(
                [sys.executable, "scripts/test-accounting-http.py"],
                env=suite_env,
                capture_output=True,
                text=True,
                timeout=40,
            )
            assert result.returncode == 0, result.stdout
            before = http.get(
                "/v1/gateway/requests",
                params={"start_time": int(time.time()) - 120},
                headers={"x-api-key": credentials["A"]},
            ).json()["data"]
            cli(
                "prices",
                "set",
                "--model",
                "fake-model",
                "--version",
                "v2",
                "--input-per-million",
                "99",
                "--output-per-million",
                "99",
            )
            cli("keys", "revoke", key_ids["A"])
            body = {"model": "fake-model", "messages": [{"role": "user", "content": "hi"}]}
            assert (
                http.post(
                    "/v1/chat/completions",
                    json=body,
                    headers={"Authorization": "Bearer " + credentials["A"]},
                ).status_code
                == 401
            )
            rotated = cli("keys", "rotate", key_ids["A2"])
            assert (
                http.get(
                    "/v1/gateway/requests",
                    params={"start_time": int(time.time()) - 120},
                    headers={"x-api-key": credentials["A2"]},
                ).status_code
                == 401
            )
            after = http.get(
                "/v1/gateway/requests",
                params={"start_time": int(time.time()) - 120},
                headers={"x-api-key": rotated["secret"]},
            ).json()["data"]
            assert [r["retail_amount"] for r in before] == [r["retail_amount"] for r in after]
            assert all(r["price_version"] == "v1" for r in after)
            cli("users", "disable", bob)
            assert (
                http.post(
                    "/v1/messages",
                    json={**body, "max_tokens": 10},
                    headers={"x-api-key": credentials["B"], "anthropic-version": "2023-06-01"},
                ).status_code
                == 401
            )
        server.terminate()
        server.wait(timeout=10)
        server = subprocess.Popen(
            [sys.executable, "-m", "endpoint_gateway", "--port", str(port)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        with httpx.Client(base_url=url, timeout=2) as http:
            for _ in range(100):
                try:
                    response = http.get(
                        "/v1/gateway/requests",
                        params={"start_time": int(time.time()) - 120},
                        headers={"x-api-key": rotated["secret"]},
                    )
                    if response.status_code == 200:
                        assert [r["retail_amount"] for r in after] == [
                            r["retail_amount"] for r in response.json()["data"]
                        ]
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.05)
            else:
                raise AssertionError("Restart did not restore reporting")
    finally:
        server.terminate()
        server.wait(timeout=10)
