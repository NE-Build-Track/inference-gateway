"""Exercise the paid acceptance driver offline before spending on its assertions."""

import importlib.util
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from inference_gateway.app import create_app
from inference_gateway.core.models import Finished
from inference_gateway.testing import FakeBackend

spec = importlib.util.spec_from_file_location(
    "live_extended", Path(__file__).parents[1] / "scripts/test-live-extended.py"
)
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


async def test_live_driver_both_sdks_modes_and_report(monkeypatch, tmp_path):
    class MeasuredFake(FakeBackend):
        async def stream(self, request):
            async for event in super().stream(request):
                yield replace(event, input_tokens=11) if isinstance(event, Finished) else event

    app = create_app(MeasuredFake({"alpha": {"value": 1}, "beta": {"value": 2}}))
    original = acceptance.request

    def request(feature, protocol, model):
        body, extra = original(feature, protocol, model)
        if feature == "reasoning":
            body["messages"][0]["content"] = "391"
        return body, extra

    monkeypatch.setattr(acceptance, "request", request)
    monkeypatch.setattr(
        acceptance,
        "AsyncOpenAI",
        lambda **kw: AsyncOpenAI(
            **kw, http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
        ),
    )
    monkeypatch.setattr(
        acceptance,
        "AsyncAnthropic",
        lambda **kw: AsyncAnthropic(
            **kw, http_client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
        ),
    )
    # The fake is explicitly signed as synthetic; Livepeer must instead emit an empty signature.
    original_verify = acceptance.verify

    def verify(feature, protocol, values):
        if feature == "reasoning" and protocol == "anthropic":
            values = (*values[:-1], "")
        return original_verify(feature, protocol, values)

    monkeypatch.setattr(acceptance, "verify", verify)
    path = tmp_path / "results.json"
    await acceptance.main(
        SimpleNamespace(
            key_file=None,
            model="fake",
            url="http://test",
            protocol=None,
            feature=None,
            mode=None,
            output=str(path),
        )
    )
    import json

    report = json.loads(path.read_text())
    assert len(report["results"]) == 16
    assert all(row["status"] == "passed" and row["request_id"] for row in report["results"])
    assert "Synthetic reasoning" not in path.read_text()
    assert "Boston" not in path.read_text()
