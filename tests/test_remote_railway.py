import asyncio
import base64
import json
import time
from contextlib import asynccontextmanager

import httpx
import pytest
from openai import AsyncOpenAI

from inference_gateway.config import Settings
from inference_gateway.remote import create_remote_app


@asynccontextmanager
async def remote(
    tmp_path,
    *,
    stale=False,
    job_status=200,
    lost=False,
    expired=False,
    multipart=False,
    closed=None,
    opened=None,
):
    schema = {
        "type": "object",
        "properties": {"model": {"const": "image-model"}, "prompt": {"type": "string"}},
        "required": ["model", "prompt"],
        "additionalProperties": False,
    }
    if multipart:
        schema = {
            "type": "object",
            "properties": {
                "model": {"const": "image-model"},
                "file": {"type": "string", "format": "binary"},
            },
            "required": ["model", "file"],
            "additionalProperties": False,
        }
    operation_path = "/v1/audio/transcriptions" if multipart else "/v1/images/generations"
    media_type = "multipart/form-data" if multipart else "application/json"
    binding = {
        "app": "images",
        "model": "image-model",
        "path": operation_path,
        "deployment_revision": "sha256:" + "a" * 64,
    }
    profile = {
        **binding,
        "content_type": media_type,
        "input_schema": schema,
        "defaults": {"model": "image-model"},
    }
    catalog = {
        "openapi": "3.1.0",
        "paths": {
            "/catalog/images/v1/images/generations": {
                "post": {
                    "operationId": "images",
                    "x-livepeer": binding,
                    "requestBody": {"content": {media_type: {"schema": schema}}},
                    "responses": {"200": {"description": "Image response"}},
                }
            }
        },
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    (tmp_path / "native-approvals.json").write_text(
        json.dumps(
            [
                {
                    **binding,
                    "endpoint": "audio_transcriptions" if multipart else "images_generations",
                    "expires_at": time.time() + (-600 if expired else 600),
                }
            ]
        )
    )
    calls = []

    class BlockedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"created":1,'
            opened.set()
            await asyncio.Event().wait()

        async def aclose(self):
            closed.append(True)

    async def handle(request):
        if request.url.path == "/readyz":
            return httpx.Response(200, json={"ready": True})
        if request.headers.get("authorization") not in {"Bearer one", "Bearer two"}:
            return httpx.Response(401)
        if request.url.path == "/v1/capabilities":
            return httpx.Response(
                200,
                json=[{**profile, "deployment_revision": "sha256:" + "b" * 64}]
                if stale
                else [profile],
            )
        assert request.url.path == "/v1/jobs"
        calls.append(request)
        if lost:
            raise httpx.ReadError("connection lost", request=request)
        return httpx.Response(
            job_status,
            headers={
                "Livepeer-Job-Id": "11111111-1111-4111-8111-111111111111",
                "content-type": "application/json",
            },
            stream=BlockedStream()
            if closed is not None
            else httpx.ByteStream(
                b'{"text":"hello"}'
                if multipart
                else b'{"created":1,"data":[{"b64_json":"aW1hZ2U="}]}'
            ),
        )

    async with httpx.AsyncClient(
        base_url="https://engine", transport=httpx.MockTransport(handle)
    ) as upstream:
        app = create_remote_app(
            Settings(
                backend="railway",
                engine_url="https://engine",
                capability_openapi_file=str(path),
                batch_database=str(tmp_path / "batch.db"),
                batch_artifact_dir=str(tmp_path / "artifacts"),
            ),
            upstream,
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                base_url="http://test", transport=httpx.ASGITransport(app)
            ) as client:
                yield client, calls


async def test_sdk_body_dispatch_replay_and_credential_isolation(tmp_path):
    async with remote(tmp_path) as (client, calls):
        sdk = AsyncOpenAI(api_key="one", base_url="http://test/v1", http_client=client)
        kwargs = {
            "model": "image-model",
            "prompt": "cat",
            "extra_headers": {"idempotency-key": "once"},
        }
        assert (await sdk.images.generate(**kwargs)).data[0].b64_json == "aW1hZ2U="
        assert (await sdk.images.generate(**kwargs)).data[0].b64_json == "aW1hZ2U="
        assert len(calls) == 1
        job = json.loads(calls[0].content)
        assert json.loads(base64.urlsafe_b64decode(job["raw_body"])) == {
            "model": "image-model",
            "prompt": "cat",
        }
        assert job["app"] == "images" and job["path"] == "/v1/images/generations"
        assert job["max_attempts"] == 1 and job["return_http_errors"]
        assert calls[0].headers["authorization"] == "Bearer one"
        result = await client.post(
            "/v1/images/generations",
            headers={"authorization": "Bearer two", "idempotency-key": "once"},
            json={"model": "image-model", "prompt": "cat"},
        )
        assert result.status_code == 200 and len(calls) == 2
        artifact = result.headers["x-artifact-id"]
        assert (
            await client.get("/v1/artifacts/" + artifact, headers={"authorization": "Bearer one"})
        ).status_code == 404
        assert (
            await client.get("/v1/artifacts/" + artifact, headers={"authorization": "Bearer two"})
        ).status_code == 200


@pytest.mark.parametrize("stale", [False, True])
async def test_auth_schema_and_stale_admission_block_before_payment(tmp_path, stale):
    async with remote(tmp_path, stale=stale) as (client, calls):
        assert (await client.get("/v1/capabilities")).status_code == 401
        assert (
            await client.get("/v1/capabilities", headers={"authorization": "Bearer bad"})
        ).status_code == 401
        result = await client.post(
            "/v1/images/generations",
            headers={"authorization": "Bearer one"},
            json={"model": "image-model", "prompt": "cat", "unsupported": True},
        )
        assert result.status_code == (503 if stale else 400)
        assert calls == []


async def test_lost_paid_submission_is_not_retried(tmp_path):
    async with remote(tmp_path, lost=True) as (client, calls):
        kwargs = {
            "headers": {"authorization": "Bearer one", "idempotency-key": "lost"},
            "json": {"model": "image-model", "prompt": "cat"},
        }
        assert (await client.post("/v1/images/generations", **kwargs)).status_code == 503
        assert (await client.post("/v1/images/generations", **kwargs)).status_code == 409
        assert len(calls) == 1


async def test_remote_refusal_preserves_status_and_openapi_is_public(tmp_path):
    async with remote(tmp_path, job_status=403) as (client, calls):
        result = await client.post(
            "/v1/images/generations",
            headers={"authorization": "Bearer one"},
            json={"model": "image-model", "prompt": "cat"},
        )
        assert result.status_code == 403 and len(calls) == 1
        spec = (await client.get("/openapi.json")).json()
        operation = spec["paths"]["/v1/images/generations"]["post"]
        assert "x-livepeer" not in operation
        assert (
            operation["requestBody"]["content"]["application/json"]["schema"][
                "additionalProperties"
            ]
            is False
        )
        assert "/v1/audio/speech" not in spec["paths"]


async def test_expired_admission_disables_models_and_dispatch(tmp_path):
    async with remote(tmp_path, expired=True) as (client, calls):
        headers = {"authorization": "Bearer one"}
        assert (await client.get("/v1/models", headers=headers)).json()["data"] == []
        response = await client.post(
            "/v1/images/generations",
            headers=headers,
            json={"model": "image-model", "prompt": "cat"},
        )
        assert response.status_code == 503 and calls == []


async def test_sdk_multipart_is_forwarded_unchanged_and_rejects_extensions(tmp_path):
    async with remote(tmp_path, multipart=True) as (client, calls):
        sdk = AsyncOpenAI(api_key="one", base_url="http://test/v1", http_client=client)
        result = await sdk.audio.transcriptions.create(
            model="image-model", file=("voice.wav", b"RIFF-test", "audio/wav")
        )
        assert result.text == "hello" and len(calls) == 1
        payload = json.loads(calls[0].content)
        assert payload["content_type"].startswith("multipart/form-data; boundary=")
        assert b"RIFF-test" in base64.urlsafe_b64decode(payload["raw_body"])
        response = await client.post(
            "/v1/audio/transcriptions",
            headers={"authorization": "Bearer one"},
            data={"model": "image-model", "unsupported": "x"},
            files={"file": ("voice.wav", b"RIFF-test", "audio/wav")},
        )
        assert response.status_code == 400 and len(calls) == 1


async def test_cancellation_closes_core_stream_and_keeps_uncertain_receipt(tmp_path):
    closed = []
    opened = asyncio.Event()
    async with remote(tmp_path, closed=closed, opened=opened) as (client, calls):
        kwargs = {
            "headers": {"authorization": "Bearer one", "idempotency-key": "cancel"},
            "json": {"model": "image-model", "prompt": "cat"},
        }
        task = asyncio.create_task(client.post("/v1/images/generations", **kwargs))
        signal = asyncio.create_task(opened.wait())
        done, _ = await asyncio.wait({task, signal}, timeout=2, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            signal.cancel()
            response = task.result()
            pytest.fail(
                f"Stream finished before cancellation: {response.status_code} {response.text}"
            )
        assert signal in done
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert closed == [True]
        assert (await client.post("/v1/images/generations", **kwargs)).status_code == 409
        assert len(calls) == 1
