import json

import pytest

from inference_gateway.backends.openai_events import CompletionDecoder
from inference_gateway.backends.sse import SSEDecoder
from inference_gateway.core.backend import BackendError
from inference_gateway.core.models import BlockEnd, BlockStart, Delta, Finished


def chunk(delta, finish=None):
    return json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})


def end(decoder, reason="tool_calls"):
    decoder.feed(chunk({}, reason))
    decoder.feed('{"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":7}}')
    return decoder.feed("[DONE]")


def call(index, arguments, *, id=None, name=None):
    item = {"index": index, "function": {"arguments": arguments}}
    if id:
        item["id"] = id
    if name:
        item["function"]["name"] = name
    return chunk({"tool_calls": [item]})


def test_first_tool_is_incremental_and_interleaved_tools_are_preserved():
    decoder = CompletionDecoder(1000)
    out = decoder.feed(chunk({"content": "Checking"}))
    assert out == [BlockStart(0), Delta(0, "Checking")]
    first = decoder.feed(call(3, '{"a":', id="one", name="first"))
    assert isinstance(first[0], BlockEnd)
    assert first[-1] == Delta(1, '{"a":')
    assert decoder.feed(call(8, '{"b":', id="two", name="second")) == []
    assert decoder.feed(call(3, "1}")) == [Delta(1, "1}")]
    assert decoder.feed(call(8, "2}")) == []
    tail = end(decoder)
    assert tail[0] == BlockEnd(1)
    assert tail[1].call.name == "second"
    assert [e.text for e in tail if isinstance(e, Delta)] == ['{"b":', "2}"]
    assert tail[-2] == BlockEnd(2)
    assert tail[-1] == Finished("tool_calls", 3, 7)


def test_arguments_before_identity_are_not_dropped():
    decoder = CompletionDecoder(1000)
    assert decoder.feed(call(0, '{"a":')) == []
    events = decoder.feed(call(0, "1}", id="one", name="first"))
    assert [e.text for e in events if isinstance(e, Delta)] == ['{"a":', "1}"]
    end(decoder)


@pytest.mark.parametrize("arguments", ["not JSON", "[]", "{", '{"x":NaN}'])
def test_invalid_tool_arguments(arguments):
    decoder = CompletionDecoder(1000)
    decoder.feed(call(0, arguments, id="a", name="tool"))
    with pytest.raises(BackendError):
        end(decoder)


def test_output_and_frame_bounds():
    decoder = CompletionDecoder(4)
    with pytest.raises(BackendError):
        decoder.feed(chunk({"content": "12345"}))
    sse = SSEDecoder(8)
    with pytest.raises(BackendError):
        sse.feed(b"data: 12345")


@pytest.mark.parametrize("separator", [b"\n", b"\r\n", b"\r"])
def test_sse_byte_boundaries_and_multiline(separator):
    data = separator.join([b": comment", b"data: first", "data: é".encode(), b"", b""])
    decoder = SSEDecoder(1000)
    frames = []
    for byte in data:
        frames.extend(decoder.feed(bytes([byte])))
    assert frames == ["first\né"]
    decoder.finish()


def test_sse_incomplete_frame():
    decoder = SSEDecoder(1000)
    decoder.feed(b"data: {}\n")
    with pytest.raises(BackendError):
        decoder.finish()


def test_runner_stop_with_valid_tool_call_is_normalized():
    decoder = CompletionDecoder(1000)
    decoder.feed(call(0, '{"city":"Boston"}', id="call_1", name="weather"))
    assert end(decoder, "stop")[-1] == Finished("tool_calls", 3, 7)


@pytest.mark.parametrize("reason,arguments", [("length", "{}"), ("stop", "{")])
def test_normalization_does_not_accept_truncated_tools(reason, arguments):
    decoder = CompletionDecoder(1000)
    decoder.feed(call(0, arguments, id="call_1", name="weather"))
    with pytest.raises(BackendError):
        end(decoder, reason)


def test_cache_usage_is_preserved_and_invalid_totals_rejected():
    decoder = CompletionDecoder(1000)
    decoder.feed(chunk({"content": "hello"}, "stop"))
    decoder.feed(
        json.dumps(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "prompt_tokens_details": {"cached_tokens": 8},
                },
            }
        )
    )
    assert decoder.feed("[DONE]")[-1] == Finished("stop", 10, 2, 8)
    decoder = CompletionDecoder(1000)
    decoder.feed(chunk({"content": "hello"}, "stop"))
    decoder.feed(
        json.dumps(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "prompt_tokens_details": {"cached_tokens": 11},
                },
            }
        )
    )
    with pytest.raises(BackendError):
        decoder.feed("[DONE]")
