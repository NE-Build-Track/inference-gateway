"""Inspect only the routing/accounting envelope; retain the original request bytes."""

import json
from collections.abc import Iterable
from typing import Any

from inference_gateway.adapters.runner_contract import PROFILES
from inference_gateway.core.native import NativeRequest


class InvalidNativeRequest(ValueError):
    pass


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise InvalidNativeRequest("Duplicate JSON keys are ambiguous")
        value[key] = item
    return value


def _invalid_constant(value: str) -> None:
    raise InvalidNativeRequest("Non-finite JSON number")


def envelope(
    body: bytes,
    *,
    path: str,
    request_id: str,
    headers: Iterable[tuple[str, str]] = (),
    query: bytes = b"",
    max_bytes: int = 8 * 1024 * 1024,
) -> NativeRequest:
    endpoint = next((k for k, v in PROFILES.items() if v["path"] == path), None)
    if endpoint is None or len(body) > max_bytes:
        raise InvalidNativeRequest("Unsupported endpoint or oversized request")
    try:
        data = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_object,
            parse_constant=_invalid_constant,
        )
        if not isinstance(data, dict):
            raise ValueError()
        model = data.get("model")
        streaming = data.get("stream", False)
        if not isinstance(model, str) or not model or model.strip() != model:
            raise ValueError()
        if type(streaming) is not bool:
            raise ValueError()
        if endpoint == "responses":
            # Explicit false avoids a backend's implicit storage default.
            if data.get("store") is not False or data.get("background", False) is not False:
                raise ValueError()
            if "previous_response_id" in data or "conversation" in data:
                raise ValueError()
        if endpoint == "chat_completions" and streaming:
            options = data.get("stream_options")
            if not isinstance(options, dict) or options.get("include_usage") is not True:
                raise ValueError()
    except (ValueError, TypeError, RecursionError):
        raise InvalidNativeRequest("Invalid native routing or accounting envelope") from None
    # Allowlist prevents credentials, Livepeer control headers and hop-by-hop fields leaking.
    allowed = {"content-type", "accept", "anthropic-version", "anthropic-beta"}
    entries = [(k.lower(), v) for k, v in headers]
    connection = {
        part.strip().lower() for k, v in entries if k == "connection" for part in v.split(",")
    }
    forwarded = tuple((k, v) for k, v in entries if k in allowed and k not in connection)
    if any("\r" in v or "\n" in v for _, v in forwarded):
        raise InvalidNativeRequest("Invalid protocol headers")
    if any(k == "content-encoding" and v.lower() != "identity" for k, v in entries):
        raise InvalidNativeRequest("Encoded request bodies are unsupported")
    if query:
        # No admitted query extensions yet; never forward an alternative auth/model source.
        raise InvalidNativeRequest("Query parameters require an admitted profile")
    return NativeRequest(request_id, model, endpoint, path, query, forwarded, body, streaming)
