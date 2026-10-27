"""Readiness observer only. Never rewrites or proxies inference requests."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.request import urlopen


def check(config: dict, fetch) -> bool:
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


def fetch(url: str) -> dict:
    with urlopen(url, timeout=3) as response:
        data = response.read(1048577)
        if len(data) > 1048576:
            raise ValueError("Oversized readiness response")
        return json.loads(data) if data else {}


def main() -> None:
    config = json.loads(Path("/app/readiness.json").read_text())

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            ready = False
            if self.path != "/healthz":
                self.send_error(404)
                return
            try:
                ready = check(config, fetch)
            except Exception:
                pass
            self.send_response(200 if ready else 503)
            self.end_headers()
            self.wfile.write(b"ready" if ready else b"not ready")

        def log_message(self, *_args) -> None:
            pass

    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


if __name__ == "__main__":
    main()
