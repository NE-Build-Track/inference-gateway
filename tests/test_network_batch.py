import json
import sqlite3
import time

import httpx
import pytest
from livepeer_builder.testing import FakeEngine, Reply, runner
from openai import AsyncOpenAI

from inference_gateway.adapters.batch_store import BatchConflict, BatchStore
from inference_gateway.adapters.runner_contract_v2 import MEDIA_PROFILES
from inference_gateway.app import create_app
from inference_gateway.config import LivepeerSettings, ModelRoute, NativeApproval, Settings


def network_app(tmp_path, operation, reply):
    metadata = {
        "inference_contract": 2,
        "runner_id": "one",
        "model_name": "network-model",
        "runtime": {"name": "comfystream", "image": "example/runner@sha256:" + "a" * 64},
        "deployment_revision": "sha256:" + "b" * 64,
        "endpoints": {operation: MEDIA_PROFILES[operation]},
        "capacity_group": {"id": "host-gpu0", "slots": 1},
        "admission": "candidate",
    }
    candidate = runner(
        "media",
        orchestrator_url="https://orch",
        capacity_available=1,
        metadata=json.dumps(metadata, separators=(",", ":")),
    ).model_copy(update={"runner_id": "one"})
    engine = FakeEngine(
        streaming=True,
        runners=[candidate],
        replies={("media", MEDIA_PROFILES[operation]["path"]): reply},
    )
    options = LivepeerSettings(
        routes=(ModelRoute(name="network-model", app="media", mode="native"),),
        native_approvals=(
            NativeApproval(
                orchestrator="https://orch",
                runner_id="one",
                app="media",
                model="network-model",
                deployment_revision=metadata["deployment_revision"],
                endpoint=operation,
                expires_at=time.time() + 600,
            ),
        ),
    )

    async def factory(_):
        return engine

    config = Settings(
        backend="livepeer",
        api_key="test-key",
        batch_database=str(tmp_path / "batch.sqlite"),
        batch_artifact_dir=str(tmp_path / "artifacts"),
    )
    return create_app(settings=config, livepeer_settings=options, engine_factory=factory), engine


@pytest.mark.asyncio
async def test_synchronous_image_uses_engine_and_replays_without_another_generation(tmp_path):
    payload = b'{"created":1,"data":[{"b64_json":"aW1hZ2U="}]}'
    app, engine = network_app(
        tmp_path, "images_generations", Reply(content_type="application/json", chunks=(payload,))
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as http:
            sdk = AsyncOpenAI(api_key="test-key", base_url="http://test/v1", http_client=http)
            assert {model.id for model in (await sdk.models.list()).data} == {"network-model"}
            kwargs = {
                "model": "network-model",
                "prompt": "cat",
                "extra_headers": {"idempotency-key": "once"},
            }
            first = await sdk.images.generate(**kwargs)
            second = await sdk.images.generate(**kwargs)
            assert first.data[0].b64_json == second.data[0].b64_json == "aW1hZ2U="
            conflict = await http.post(
                "/v1/images/generations",
                headers={"authorization": "Bearer test-key", "idempotency-key": "once"},
                json={"model": "network-model", "prompt": "different"},
            )
            assert conflict.status_code == 409
            with sqlite3.connect(tmp_path / "batch.sqlite") as db:
                db.row_factory = sqlite3.Row
                row = db.execute("SELECT * FROM batch_requests").fetchone()
            assert row["network_job_id"] and row["state"] == "succeeded"
            assert json.loads(row["usage"])["output_bytes"] == len(payload)
            artifact = "/v1/artifacts/" + row["response_artifact"]
            assert (await http.get(artifact)).status_code == 401
            assert (
                await http.get(artifact, headers={"authorization": "Bearer test-key"})
            ).content == payload
            assert (
                await http.post("/v1/jobs", json={"capability": "flux-image", "input": {}})
            ).status_code == 404
    assert len(engine.jobs.requests) == 1
    dispatched = engine.jobs.requests[0][1]
    assert dispatched.app == "media" and dispatched.model == "network-model"
    assert dispatched.capability == "native.http.v1"
    assert "authorization" not in dict(dispatched.headers)
    assert engine.jobs.usage_reports == []  # Media is never represented as fabricated tokens.
    restarted, restarted_engine = network_app(
        tmp_path, "images_generations", Reply(content_type="application/json", chunks=(payload,))
    )
    async with restarted.router.lifespan_context(restarted):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(restarted), base_url="http://test"
        ) as http:
            sdk = AsyncOpenAI(api_key="test-key", base_url="http://test/v1", http_client=http)
            replayed = await sdk.images.generate(**kwargs)
            assert replayed.data[0].b64_json == "aW1hZ2U="
    assert restarted_engine.jobs.requests == []


@pytest.mark.asyncio
async def test_multipart_transcription_keeps_network_body_and_semantic_idempotency(tmp_path):
    app, engine = network_app(
        tmp_path,
        "audio_transcriptions",
        Reply(content_type="application/json", chunks=(b'{"text":"transcript"}',)),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as http:
            sdk = AsyncOpenAI(api_key="test-key", base_url="http://test/v1", http_client=http)
            for _ in range(2):
                result = await sdk.audio.transcriptions.create(
                    model="network-model",
                    file=("audio.wav", b"fixture", "audio/wav"),
                    extra_headers={"idempotency-key": "one-upload"},
                )
                assert result.text == "transcript"
    assert len(engine.jobs.requests) == 1
    dispatched = engine.jobs.requests[0][1]
    assert b"fixture" in dispatched.raw_body
    assert dispatched.content_type.startswith("multipart/form-data; boundary=")


def test_batch_store_recovery_is_tenant_scoped_and_never_replays_incomplete_jobs(tmp_path):
    database = str(tmp_path / "batch.sqlite")
    artifacts = str(tmp_path / "artifacts")
    store = BatchStore(database, artifacts)
    row, fresh = store.begin("alice", "m", "audio_speech", "fingerprint", "key")
    assert fresh
    store.bind(row["id"], "network-job")
    store.close()
    store = BatchStore(database, artifacts)
    store.recover()
    try:
        recovered, fresh = store.begin("alice", "m", "audio_speech", "fingerprint", "key")
        assert not fresh and recovered["error"] == "upstream_outcome_unknown"
        with pytest.raises(BatchConflict):
            store.replay("alice", recovered)
        other, fresh = store.begin("bob", "m", "audio_speech", "fingerprint", "key")
        assert fresh and other["id"] != row["id"]
        asset = store.put_artifact("alice", row["id"], b"wav", "audio/wav")
        with pytest.raises(KeyError):
            store.artifact("bob", asset)
        store.db.execute("UPDATE batch_artifacts SET expires=0")
        store.expire()
        assert not (tmp_path / "artifacts" / asset).exists()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_speech_returns_and_persists_network_binary_without_a_job_envelope(tmp_path):
    waveform = b"RIFF\x24\x00\x00\x00WAVEfmt network fixture"
    app, engine = network_app(
        tmp_path, "audio_speech", Reply(content_type="audio/wav", chunks=(waveform,))
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as http:
            response = await http.post(
                "/v1/audio/speech",
                headers={"authorization": "Bearer test-key"},
                json={"model": "network-model", "input": "Hello", "voice": "alloy"},
            )
            assert response.status_code == 200 and response.content == waveform
            assert response.headers["content-type"] == "audio/wav"
            asset = await http.get(
                "/v1/artifacts/" + response.headers["x-artifact-id"],
                headers={"authorization": "Bearer test-key"},
            )
            assert asset.content == waveform
    assert len(engine.jobs.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content_type", "body", "status"),
    [
        ("application/json-invalid", b"{}", 415),
        ("multipart/form-data", b"missing boundary", 400),
        ("application/json", b'{"model":"network-model","stream":0}', 400),
        ("application/json", b'{"model":"network-model","model":"other"}', 400),
        ("application/json", b'{"model":"network-model","seed":NaN}', 400),
    ],
)
async def test_invalid_batch_inputs_do_not_start_paid_jobs(tmp_path, content_type, body, status):
    app, engine = network_app(tmp_path, "images_generations", Reply())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as http:
            response = await http.post(
                "/v1/images/generations",
                headers={"authorization": "Bearer test-key", "content-type": content_type},
                content=body,
            )
            assert response.status_code == status
    assert engine.jobs.requests == []


@pytest.mark.asyncio
async def test_network_failure_response_replays_without_resubmission(tmp_path):
    payload = b'{"error":{"message":"admitted upstream error","type":"invalid_request_error"}}'
    app, engine = network_app(
        tmp_path,
        "images_generations",
        Reply(status_code=422, content_type="application/json", chunks=(payload,)),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as http:
            first = None
            for _ in range(2):
                response = await http.post(
                    "/v1/images/generations",
                    headers={"authorization": "Bearer test-key", "idempotency-key": "rejected"},
                    json={"model": "network-model", "prompt": "cat"},
                )
                assert response.status_code == 422 and response.content == payload
                assert response.headers["cache-control"] == "private, no-store"
                if first is None:
                    first = response
                else:
                    assert response.headers["x-batch-id"] == first.headers["x-batch-id"]
                    assert response.headers["x-artifact-id"] == first.headers["x-artifact-id"]
    assert len(engine.jobs.requests) == 1
