"""Inspect only the routing/accounting envelope; retain the original request bytes."""

import base64
import json
import re
from collections.abc import Iterable
from typing import Any

from inference_gateway.core.native import NativeRequest

# The gateway's native surfaces. Each endpoint's wire profile, and the path runners serve it on,
# come from the registry in the backend.
SURFACES = {
    "/v1/chat/completions": "chat_completions",
    "/v1/responses": "responses",
    "/v1/messages": "messages",
    "/v1/systemone": "systemone",
}

# Backends ignore or fetch these depending on the backend and model. An ignored field still returns
# an answer and a charge, so a decision would silently not use the media it was sent.
SYSTEM_ONE_UNSUPPORTED = ("videos", "audio", "files", "assets", "media_kwargs")
DATA_IMAGE = re.compile(r"data:image/[a-z0-9.+-]+;base64,")
IMAGE_SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")


def _inline_image(value: Any) -> bool:
    """Bytes in the request, never a location: some backends open paths and fetch URLs in `images`.

    Which inline form works is the backend's call, so both forms pass. Base64 text cannot contain
    `.`, so even a bare JPEG, which SGLang reads as a path under `/9j/`, cannot reach a real file.
    """
    if not isinstance(value, str):
        return False
    prefix = DATA_IMAGE.match(value)
    try:
        raw = base64.b64decode(value[prefix.end() :] if prefix else value, validate=True)
    except ValueError:
        return False
    return raw.startswith(IMAGE_SIGNATURES) or (raw[:4] == b"RIFF" and raw[8:12] == b"WEBP")


def _inline_images(images: Any) -> bool:
    if not isinstance(images, list):
        return False
    for image in images:
        if isinstance(image, dict):
            if not image.keys() <= {"url", "detail"} or not isinstance(
                image.get("detail", ""), str
            ):
                return False
            image = image.get("url")
        if not _inline_image(image):
            return False
    return True


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
    endpoint = SURFACES.get(path)
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
        if endpoint == "systemone":
            # Decisions have no stream: some backends ignore the flag and answer with plain JSON.
            if streaming or any(field in data for field in SYSTEM_ONE_UNSUPPORTED):
                raise ValueError()
            if "images" in data and not _inline_images(data["images"]):
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
