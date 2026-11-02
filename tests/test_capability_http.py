import asyncio

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from stream_pack.catalog import load_catalog

from inference_gateway.adapters.capability_http import router
from inference_gateway.adapters.capability_jobs import Jobs


class ArtifactExecutor:
    def __init__(self, root):
        self.root = root

    async def execute(self, capability, inputs, *, timeout):  # noqa: ASYNC109
        if capability == "whisper":
            return {"text": "transcript", "artifacts": []}
        self.root.mkdir(exist_ok=True)
        path = self.root / "output.png"
        path.write_bytes(b"image")
        return {"artifacts": [{"path": str(path), "media_type": "image/png"}]}


@pytest.mark.asyncio
async def test_sdk_image_shape_schema_and_artifact_ownership(tmp_path):
    jobs = Jobs(
        str(tmp_path / "jobs.sqlite"),
        str(tmp_path / "artifacts"),
        ArtifactExecutor(tmp_path / "outputs"),
        load_catalog(),
        output_root=str(tmp_path / "outputs"),
    )
    app = FastAPI()

    async def owner(request: Request):
        key = request.headers.get("authorization", "")
        if key not in {"Bearer alice", "Bearer bob"}:
            raise HTTPException(401)
        return key

    app.include_router(router(jobs, owner, str(tmp_path / "inputs")))
    await jobs.start()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            assert (await client.get("/v1/capabilities")).status_code == 401
            headers = {"authorization": "Bearer alice"}
            response = await client.post(
                "/v1/images/generations",
                headers=headers,
                json={"model": "black-forest-labs/FLUX.2-klein-4B", "prompt": "cat"},
            )
            assert response.status_code == 200, response.text
            assert response.json()["data"] == [{"b64_json": "aW1hZ2U="}]
            schema = (await client.get("/openapi.json")).json()
            assert schema["openapi"].startswith("3.1")
            assert schema["paths"]["/v1/images/generations"]["post"]["requestBody"]
            job = (
                await client.post(
                    "/v1/jobs",
                    headers=headers,
                    json={"capability": "flux-image", "input": {"prompt": "cat"}},
                )
            ).json()
            for _ in range(100):
                value = jobs.get("Bearer alice", job["id"])
                if value["status"] == "succeeded":
                    break
                await asyncio.sleep(0.01)
            url = value["result"]["artifacts"][0]["url"]
            assert (
                await client.get(url, headers={"authorization": "Bearer bob"})
            ).status_code == 404
            assert (await client.get(url, headers=headers)).content == b"image"
            invalid = await client.post(
                "/v1/images/generations",
                headers=headers,
                json={
                    "model": "black-forest-labs/FLUX.2-klein-4B",
                    "prompt": "cat",
                    "unrecognized": True,
                },
            )
            assert invalid.status_code == 400
            assert "error" in invalid.json()
            from openai import AsyncOpenAI

            sdk = AsyncOpenAI(api_key="alice", base_url="http://test/v1", http_client=client)
            generated = await sdk.images.generate(
                model="black-forest-labs/FLUX.2-klein-4B",
                prompt="cat",
                size="512x512",
                n=1,
                response_format="b64_json",
            )
            assert generated.data[0].b64_json == "aW1hZ2U="
            transcription = await sdk.audio.transcriptions.create(
                model="openai/whisper-large-v3-turbo",
                file=("test.wav", b"audio fixture", "audio/wav"),
            )
            assert transcription.text == "transcript"
    finally:
        await jobs.close()
