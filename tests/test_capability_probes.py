"""Probe evidence must not turn accepted fields or outages into capability claims."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "probes", Path(__file__).parents[1] / "scripts/probe-livepeer-capabilities.py"
)
probes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probes)


@pytest.mark.parametrize(
    "feature",
    [
        "top_p",
        "top_k",
        "seed",
        "penalties",
        "hosted_tools",
        "audio_input",
        "reasoning_effort_low",
        "reasoning_budget",
    ],
)
def test_http_success_does_not_prove_semantic_support(feature):
    signals = {"done": True, "usage": True, "finish": True, "text": True}
    assert probes.classify(feature, signals, "hello")[0] == "inconclusive"


def test_missing_terminal_or_usage_is_unsupported_response():
    assert (
        probes.classify("baseline", {"done": True, "usage": False}, "hello")[0]
        == "Unsupported Response"
    )


def test_schema_probe_checks_actual_value():
    signals = {"done": True, "usage": True, "finish": True}
    assert probes.classify("structured_output", signals, '{"answer":"blue"}')[0] == "observed"
    assert (
        probes.classify("structured_output", signals, '{"answer":"red"}')[0]
        == "Unsupported Response"
    )


def test_reasoning_must_have_separate_content():
    signals = {"done": True, "usage": True, "finish": True, "reasoning": False}
    assert (
        probes.classify("reasoning", signals, "I think the answer is 391")[0]
        == "Unsupported Response"
    )


@pytest.mark.parametrize("terminal", [True, False])
async def test_probe_completes_or_disconnects_and_closes(terminal):
    from types import SimpleNamespace

    from inference_gateway.config import LivepeerSettings, ModelRoute

    class Stream:
        job = SimpleNamespace(id="fixture-job")
        status_code = 200
        content_type = "text/event-stream"
        complete_count = 0
        disconnected_count = 0
        closed = False
        iterator_closed = False

        def __aiter__(self):
            return self.iterate()

        async def iterate(self):
            try:
                yield (
                    b'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":"stop"}],'
                    b'"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n'
                )
                if terminal:
                    yield b"data: [DONE]\n\n"
            finally:
                self.iterator_closed = True

        async def complete(self):
            self.complete_count += 1

        async def disconnected(self):
            self.disconnected_count += 1

        async def aclose(self):
            self.closed = True

    stream = Stream()

    class Streaming:
        async def stream(self, actor, request):
            assert request.max_attempts == 1
            return stream

    engine = SimpleNamespace(jobs=SimpleNamespace(streaming=Streaming()))
    route = ModelRoute(name="fake", app="test/chat")
    result = await probes.probe(engine, LivepeerSettings(routes=(route,)), route, "baseline", {})
    assert result["status"] == ("observed" if terminal else "Unsupported Response")
    assert stream.complete_count == int(terminal)
    assert stream.disconnected_count == int(not terminal)
    assert stream.closed and stream.iterator_closed


def test_image_probe_uses_semantic_assertion():
    signals = {"done": True, "usage": True, "finish": True}
    assert probes.classify("image_input", signals, "Blue.")[0] == "observed"
    assert probes.classify("image_input", signals, "red")[0] == "Unsupported Response"


def test_audio_fixture_has_valid_header_and_duration():
    import base64
    import io
    import wave

    with wave.open(io.BytesIO(base64.b64decode(probes.audio_fixture()))) as audio:
        assert audio.getnframes() == audio.getframerate() == 16000
        assert audio.getsampwidth() == 2 and audio.getnchannels() == 1
