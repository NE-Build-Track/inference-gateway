"""Opt-in live vision probe. Never prints prompts, image bytes or response content."""

import argparse
import base64
import json
import re
import struct
import time
import zlib
from pathlib import Path

import httpx

from inference_gateway.adapters.native_usage import UsageObserver


def png(color):
    def chunk(kind, payload):
        return (
            struct.pack("!I", len(payload))
            + kind
            + payload
            + struct.pack("!I", zlib.crc32(kind + payload))
        )

    rgb = b"\xff\x00\x00" if color == "red" else b"\x00\x00\xff"
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!IIBBBBB", 224, 224, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + rgb * 224) * 224))
        + chunk(b"IEND", b"")
    )


def payload(model, endpoint, streaming, image):
    prompt = (
        "Name the dominant color of the supplied image. Answer exactly one of red, blue, green."
    )
    data = base64.b64encode(image).decode()
    body = {"model": model, "stream": streaming, "max_tokens": 128, "temperature": 0}
    if endpoint == "responses":
        body.pop("max_tokens")
        body.update(
            store=False,
            max_output_tokens=128,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": "data:image/png;base64," + data},
                    ],
                }
            ],
        )
    else:
        part = (
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + data}}
            if endpoint == "chat_completions"
            else {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": data},
            }
        )
        body["messages"] = [{"role": "user", "content": [{"type": "text", "text": prompt}, part]}]
    if endpoint == "messages":
        body["thinking"] = {"type": "disabled"}
    else:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    if endpoint == "chat_completions" and streaming:
        body["stream_options"] = {"include_usage": True}
    return body


def response_text(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("text", "content") and isinstance(item, str):
                yield item
            elif (
                key == "delta"
                and value.get("type") == "response.output_text.delta"
                and isinstance(item, str)
            ):
                yield item
            elif isinstance(item, (dict, list)):
                yield from response_text(item)
    elif isinstance(value, list):
        for item in value:
            yield from response_text(item)


def stream_text(endpoint, events):
    parts = []
    for event in events:
        if endpoint == "chat_completions":
            parts.extend(
                choice.get("delta", {}).get("content") or "" for choice in event.get("choices", [])
            )
        elif endpoint == "responses" and event.get("type") == "response.output_text.delta":
            parts.append(event["delta"])
        elif endpoint == "messages" and event.get("type") == "content_block_delta":
            if event.get("delta", {}).get("type") == "text_delta":
                parts.append(event["delta"]["text"])
    return "".join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--direct", required=True)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint", choices=("chat_completions", "responses", "messages"))
    parser.add_argument(
        "--gateway-pause",
        type=float,
        default=16,
        help="Wait for single-slot discovery refresh between paid calls",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    secret = args.key_file.read_text().strip()
    rows = []
    paths = {
        "chat_completions": "/v1/chat/completions",
        "responses": "/v1/responses",
        "messages": "/v1/messages",
    }
    if args.endpoint:
        paths = {args.endpoint: paths[args.endpoint]}
    with httpx.Client(timeout=180) as client:
        for _ in range(60):
            catalog = client.get(
                args.gateway.rstrip("/") + "/v1/models",
                headers={"authorization": "Bearer " + secret},
            )
            if catalog.status_code == 200 and any(
                row.get("id") == args.model for row in catalog.json().get("data", [])
            ):
                break
            time.sleep(1)
        else:
            raise SystemExit("Model not admitted; no inference submitted")
        for endpoint, path in paths.items():
            for streaming in (False, True):
                for color in ("red", "blue"):
                    for route, base in (("direct", args.direct), ("gateway", args.gateway)):
                        headers = {"anthropic-version": "2023-06-01"}
                        if route == "gateway":
                            headers["authorization"] = "Bearer " + secret
                            headers["x-api-key"] = secret
                            time.sleep(args.gateway_pause)
                        row = {
                            "endpoint": endpoint,
                            "streaming": streaming,
                            "fixture": color,
                            "route": route,
                        }
                        try:
                            r = client.post(
                                base.rstrip("/") + path,
                                headers=headers,
                                json=payload(args.model, endpoint, streaming, png(color)),
                            )
                            row.update(
                                status=r.status_code, request_id=r.headers.get("x-request-id")
                            )
                            if r.status_code == 200:
                                observer = UsageObserver(endpoint, streaming)
                                observer.feed(r.content)
                                usage = observer.finish()
                                row["usage"] = {
                                    "input_tokens": usage.input_tokens,
                                    "output_tokens": usage.output_tokens,
                                }
                                events = (
                                    [
                                        json.loads(line[5:])
                                        for line in r.text.splitlines()
                                        if line.startswith("data:") and line[5:].strip() != "[DONE]"
                                    ]
                                    if streaming
                                    else [r.json()]
                                )
                                text = (
                                    stream_text(endpoint, events)
                                    if streaming
                                    else "".join(response_text(events))
                                ).lower()
                                row["image_understood"] = bool(
                                    re.search(r"\b" + color + r"\b", text)
                                ) and not re.search(
                                    r"\b" + ("blue" if color == "red" else "red") + r"\b", text
                                )
                                row["passed"] = (
                                    row["image_understood"]
                                    and usage.input_tokens > 0
                                    and usage.output_tokens > 0
                                )
                            else:
                                row["passed"] = False
                        except Exception as exc:
                            row.update(passed=False, error_type=type(exc).__name__)
                        rows.append(row)
                        args.output.write_text(json.dumps(rows, indent=2) + "\n")
                        print(json.dumps(row), flush=True)
        for endpoint, path in paths.items():
            for route, base in (("direct", args.direct), ("gateway", args.gateway)):
                headers = {"anthropic-version": "2023-06-01"}
                if route == "gateway":
                    headers["authorization"] = "Bearer " + secret
                    headers["x-api-key"] = secret
                    time.sleep(args.gateway_pause)
                r = client.post(
                    base.rstrip("/") + path,
                    headers=headers,
                    json=payload(args.model, endpoint, False, b"invalid-image"),
                )
                row = {
                    "endpoint": endpoint,
                    "route": route,
                    "fixture": "invalid",
                    "status": r.status_code,
                    "request_id": r.headers.get("x-request-id"),
                    "passed": r.status_code == 400,
                }
                rows.append(row)
                args.output.write_text(json.dumps(rows, indent=2) + "\n")
                print(json.dumps(row), flush=True)
    raise SystemExit(0 if all(r["passed"] for r in rows) else 1)


if __name__ == "__main__":
    main()
