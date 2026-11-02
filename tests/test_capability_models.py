import httpx
import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from stream_pack.catalog import load_catalog

from inference_gateway.adapters.capability_jobs import Jobs
from inference_gateway.app import create_app
from inference_gateway.backends.comfystream import ComfyStreamBackend
from inference_gateway.config import Settings


class LocalClient:
    async def execute(self, capability, inputs, *, timeout):  # noqa: ASYNC109
        return {
            "text": "A local answer",
            "artifacts": [],
            "usage": {"input_tokens": 5, "output_tokens": 4},
        }


@pytest.mark.asyncio
async def test_local_sdk_conversations_model_views_and_openapi_references(tmp_path):
    local = LocalClient()
    jobs = Jobs(
        str(tmp_path / "jobs.sqlite"),
        str(tmp_path / "artifacts"),
        local,
        load_catalog(),
        output_root=str(tmp_path / "outputs"),
    )
    app = create_app(ComfyStreamBackend(local), Settings(api_key="test-key"), capability_jobs=jobs)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as http:
            openai = AsyncOpenAI(api_key="test-key", base_url="http://test/v1", http_client=http)
            anthropic = AsyncAnthropic(api_key="test-key", base_url="http://test", http_client=http)
            assert {model.id for model in (await openai.models.list()).data} == {
                spec.document["model"]["source"] for spec in load_catalog().values()
            }
            assert [model.id for model in (await anthropic.models.list()).data] == [
                "Qwen/Qwen3.8-27B"
            ]
            completion = await openai.chat.completions.create(
                model="Qwen/Qwen3.8-27B", messages=[{"role": "user", "content": "Hello"}]
            )
            assert completion.choices[0].message.content == "A local answer"
            assert completion.usage.prompt_tokens == 5 and completion.usage.completion_tokens == 4
            response = await openai.responses.create(
                model="Qwen/Qwen3.8-27B", input="Hello", store=False
            )
            assert response.output_text == "A local answer"
            message = await anthropic.messages.create(
                model="Qwen/Qwen3.8-27B",
                max_tokens=128,
                messages=[{"role": "user", "content": "Hello"}],
            )
            assert message.content[0].text == "A local answer"
            schema = (await http.get("/openapi.json")).json()
            assert schema["openapi"] == "3.1.0"

            def check_refs(value):
                if isinstance(value, dict):
                    if "$ref" in value:
                        reference = value["$ref"]
                        assert reference.startswith("#/")
                        target = schema
                        for part in reference[2:].split("/"):
                            target = target[part.replace("~1", "/").replace("~0", "~")]
                    for item in value.values():
                        check_refs(item)
                elif isinstance(value, list):
                    for item in value:
                        check_refs(item)

            check_refs(schema)
    finally:
        await jobs.close()
