"""Readiness for the node's HealthURL, as runner contract v1 requires.

`/healthz` answers 200 with `{"status":"ready"}` only after a self-test of every advertised profile
passes, and 503 with `{"status":"starting"}` or `{"status":"failed"}` otherwise. The self-test sends
one small request per endpoint to the runtime each time the model becomes available; this service
never proxies the node's traffic. A runtime that loses its model returns health to 503.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

PROMPT = "Reply with the word ok."
QUESTION = {"type": "noul", "instructions": "Is the text about the sky?"}
# One small request per profile, and what a served answer must hold. Work reports (usage) are
# required because the gateway refuses answers without them.
SELF_TESTS = {
    "chat_completions": (
        {"messages": [{"role": "user", "content": PROMPT}], "max_tokens": 8, "stream": False},
        lambda answer: isinstance(answer.get("choices"), list) and bool(answer["choices"]),
    ),
    "responses": (
        {"input": PROMPT, "max_output_tokens": 16, "store": False, "stream": False},
        lambda answer: answer.get("status") in {"completed", "incomplete"},
    ),
    "messages": (
        {"messages": [{"role": "user", "content": PROMPT}], "max_tokens": 8},
        lambda answer: answer.get("type") == "message",
    ),
    "systemone": (
        {"state": "The sky is blue.", "questions": {"sky": QUESTION}},
        lambda answer: isinstance(answer.get("answers"), dict) and "sky" in answer["answers"],
    ),
}
INTERVAL_SECONDS = 10


def check(config: dict, fetch) -> bool:
    """Whether the runtime currently lists the model (for Ollama, loaded at the pinned digest)."""
    catalog = fetch(config["url"] + "/v1/models")
    if not any(row.get("id") == config["model"] for row in catalog.get("data", [])):
        return False
    if config["runtime"] == "ollama":
        # Listing an installed model is not enough: require it loaded in memory.
        loaded = fetch(config["url"] + "/api/ps")
        return any(
            row.get("name") == config["model"]
            and "sha256:" + row.get("digest", "").removeprefix("sha256:")
            == config["model_revision"]
            for row in loaded.get("models", [])
        )
    fetch(config["url"] + "/health")
    return True


def self_test(config: dict, post) -> bool:
    """Every advertised endpoint answers its small request with a served, usage-bearing answer."""
    for endpoint in config["endpoints"]:
        body, served = SELF_TESTS[endpoint["name"]]
        status, answer = post(config["url"] + endpoint["path"], {"model": config["model"], **body})
        if status != 200 or not isinstance(answer, dict) or "usage" not in answer:
            return False
        if not served(answer):
            return False
    return True


class Readiness:
    """`starting` until the first self-test passes; `failed` when one fails or the model goes."""

    def __init__(self, config: dict, fetch, post) -> None:
        self.config, self.fetch, self.post = config, fetch, post
        self.state = "starting"
        self.tested = False

    def step(self) -> str:
        try:
            visible = check(self.config, self.fetch)
        except Exception:
            visible = False
        if not visible:
            self.tested = False
            if self.state == "ready":
                self.state = "failed"
            return self.state
        if not self.tested:
            try:
                passed = self_test(self.config, self.post)
            except Exception:
                passed = False
            self.tested = passed  # a failed self-test runs again on the next step
            self.state = "ready" if passed else "failed"
        return self.state


def answer(state: str) -> tuple[int, bytes]:
    """The `livepeer.health.v1` answer for a state."""
    return (200 if state == "ready" else 503), json.dumps({"status": state}).encode()


def fetch(url: str) -> dict:
    with urlopen(url, timeout=3) as response:
        data = response.read(1048577)
        if len(data) > 1048576:
            raise ValueError("Oversized readiness response")
        return json.loads(data) if data else {}


def post(url: str, body: dict) -> tuple[int, object]:
    request = Request(
        url, data=json.dumps(body).encode(), headers={"content-type": "application/json"}
    )
    try:
        with urlopen(request, timeout=120) as response:
            data = response.read(1048577)
            if len(data) > 1048576:
                raise ValueError("Oversized self-test answer")
            return response.status, json.loads(data)
    except HTTPError as error:
        return error.code, None


def main() -> None:
    config = json.loads(Path("/app/readiness.json").read_text())
    readiness = Readiness(config, fetch, post)

    def observe() -> None:
        while True:
            readiness.step()
            time.sleep(INTERVAL_SECONDS)

    threading.Thread(target=observe, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/healthz":
                self.send_error(404)
                return
            status, body = answer(readiness.state)
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            pass

    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


if __name__ == "__main__":
    main()
