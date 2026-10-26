"""Paid, opt-in runner contract probes through Builder Core; never log response bodies.

Run in the gateway environment with --run. Each case is one operation with one
attempt. The isolated engine database retains probe/payment evidence. No running
gateway configuration or feature gate is changed by this program.
"""

import argparse
import asyncio
import base64
import importlib.metadata
import io
import json
import logging
import os
import struct
import tempfile
import wave
import zlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import anyio
from livepeer_builder import ActorContext, JobFailed, JobRequest

from inference_gateway.backends.sse import SSEDecoder
from inference_gateway.config import Settings
from inference_gateway.runtime import build_engine, load_livepeer_settings


def image_fixture():
    """A valid 128px blue RGB PNG; no external image URL or private content."""

    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    raw = (b"\x00" + b"\x00\x00\xff" * 128) * 128
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 128, 128, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return base64.b64encode(png).decode()


def audio_fixture():
    """One second of valid mono 16kHz WAV silence; structural acceptance only."""
    data = io.BytesIO()
    with wave.open(data, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(bytes(32000))
    return base64.b64encode(data.getvalue()).decode()


SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "enum": ["blue"]}},
    "required": ["answer"],
    "additionalProperties": False,
}


def cases():
    return {
        "baseline": {},
        "top_p": {"top_p": 0.5},
        "top_k": {"top_k": 10},
        "seed": {"seed": 42},
        "reasoning_effort_low": {
            "reasoning_effort": "low",
            "chat_template_kwargs": {"enable_thinking": True},
            "max_tokens": 256,
        },
        "reasoning_effort_high": {
            "reasoning_effort": "high",
            "chat_template_kwargs": {"enable_thinking": True},
            "max_tokens": 256,
        },
        "reasoning_budget": {
            "thinking_token_budget": 32,
            "chat_template_kwargs": {"enable_thinking": True},
            "max_tokens": 256,
        },
        "penalties": {"presence_penalty": 0.5, "frequency_penalty": 0.5},
        "stop": {
            "messages": [{"role": "user", "content": "Output exactly: FIRST SECOND THIRD"}],
            "stop": ["SECOND"],
        },
        "structured_output": {
            "messages": [{"role": "user", "content": "Return the requested JSON object."}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "probe", "strict": True, "schema": SCHEMA},
            },
        },
        "reasoning": {
            "chat_template_kwargs": {"enable_thinking": True},
            "max_tokens": 256,
            "messages": [
                {"role": "user", "content": "What is 17 times 23? Think briefly and answer."}
            ],
        },
        "parallel_tools": {
            "messages": [{"role": "user", "content": "Call both a and b now."}],
            "tools": [
                {
                    "type": "function",
                    "function": {"name": n, "parameters": {"type": "object", "properties": {}}},
                }
                for n in ("a", "b")
            ],
            "tool_choice": "required",
            "parallel_tool_calls": True,
        },
        "citations": {
            "messages": [
                {
                    "role": "user",
                    "content": "Describe https://example.com with a URL citation annotation.",
                }
            ]
        },
        "hosted_tools": {
            "web_search_options": {},
            "messages": [
                {
                    "role": "user",
                    "content": "Use web search to describe example.com with citations.",
                }
            ],
        },
        "image_input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "What is the dominant color? Reply with one color word.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64," + image_fixture()},
                        },
                    ],
                }
            ]
        },
        "audio_input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "format": "wav",
                                "data": audio_fixture(),
                            },
                        }
                    ],
                }
            ]
        },
        "audio_output": {
            "modalities": ["text", "audio"],
            "audio": {"voice": "alloy", "format": "wav"},
        },
        # This is explicitly a gateway extension, not native Chat Completions.
        "image_output": {"gateway": {"output_media": ["image"]}},
    }


def inspect_frames(frames):
    """Return bounded signals plus transient content for assertions; never persist content."""
    text, reasoning, calls, annotations, audio, image = "", "", set(), 0, False, False
    usage, finished, done = False, False, False
    for raw in frames:
        if raw == "[DONE]":
            done = True
            continue
        chunk = json.loads(raw)
        if chunk.get("error"):
            return {"runner_error": True}, ""
        usage = usage or isinstance(chunk.get("usage"), dict)
        for choice in chunk.get("choices", []):
            finished = finished or choice.get("finish_reason") in ("stop", "length", "tool_calls")
            delta = choice.get("delta", {})
            text += delta.get("content") or ""
            reasoning += delta.get("reasoning_content") or delta.get("reasoning") or ""
            for call in delta.get("tool_calls") or []:
                calls.add(call.get("index"))
            annotations += len(delta.get("annotations") or [])
            if isinstance(delta.get("audio"), dict) and delta["audio"].get("data"):
                audio = bool(base64.b64decode(delta["audio"]["data"], validate=True))
            image = image or bool(delta.get("images") or delta.get("gateway_blocks"))
    return {
        "done": done,
        "usage": usage,
        "finish": finished,
        "text": bool(text),
        "reasoning": bool(reasoning),
        "tool_calls": len(calls),
        "annotations": annotations,
        "audio": audio,
        "image": image,
    }, text


def classify(feature, signals, text):
    if signals.get("runner_error"):
        return "inconclusive", "Runner error; inspect engine evidence separately"
    if not all(signals.get(k) for k in ("done", "usage", "finish")):
        return "Unsupported Response", "Missing required stream termination or usage"
    if feature == "baseline":
        return (
            ("observed", "Text stream completed")
            if signals["text"]
            else ("Unsupported Response", "No text")
        )
    if feature == "structured_output":
        try:
            matches = json.loads(text) == {"answer": "blue"}
        except ValueError:
            matches = False
        return (
            ("observed", "Output matched probe schema")
            if matches
            else ("Unsupported Response", "Output did not match requested schema")
        )
    signal = {
        "reasoning": "reasoning",
        "audio_output": "audio",
        "image_output": "image",
        "citations": "annotations",
    }.get(feature)
    if signal:
        return (
            ("observed", "Requested wire field observed")
            if signals[signal]
            else ("Unsupported Response", "Requested wire field absent in this probe")
        )
    if feature == "parallel_tools":
        return (
            ("observed", "Multiple tool indices observed; interleaving not yet established")
            if signals["tool_calls"] >= 2
            else ("inconclusive", "Model did not produce multiple calls")
        )
    if feature == "stop":
        return (
            ("observed", "Controlled output stopped before marker")
            if "FIRST" in text and "SECOND" not in text and "THIRD" not in text
            else ("inconclusive", "Stop behavior not established")
        )
    if feature == "image_input":
        return (
            ("observed", "Controlled image color recognized")
            if text.strip().lower().strip(".!\n ") == "blue"
            else ("Unsupported Response", "Controlled image color not recognized")
        )
    if feature == "hosted_tools":
        return "inconclusive", "Hosted execution requires provider execution evidence"
    return "inconclusive", "Request accepted; semantic support not established"


async def probe(engine, settings, route, feature, changes):
    payload = {
        "messages": [{"role": "user", "content": "Say hello briefly."}],
        "max_tokens": 96,
        "stream": True,
        "stream_options": {"include_usage": True},
        "chat_template_kwargs": {"enable_thinking": False},
        **changes,
    }
    if route.upstream_model:
        payload["model"] = route.upstream_model
    operation = "probe_" + uuid4().hex
    actor = ActorContext(
        actor_id=settings.actor_id,
        application_id=settings.application_id,
        permissions=frozenset({"discover", "jobs.run", "jobs.read", "costs.read"}),
    )
    row = {"feature": feature, "operation_ref": operation}
    stream = None
    iterator = None
    completed = False
    try:
        async with asyncio.timeout(90):
            stream = await engine.jobs.streaming.stream(
                actor,
                JobRequest(
                    app=route.app,
                    path="/v1/chat/completions",
                    method="POST",
                    model=route.name,
                    payload=payload,
                    operation_ref=operation,
                    max_attempts=1,
                    timeout_s=80,
                    first_byte_timeout_s=40,
                    idle_timeout_s=30,
                ),
            )
            row["job_id"] = str(stream.job.id)
            row["http_status"] = stream.status_code
            if stream.status_code != 200 or not stream.content_type.startswith("text/event-stream"):
                row.update(
                    status="inconclusive", detail="Runner returned non-success or non-SSE response"
                )
                return row
            decoder = SSEDecoder(1024 * 1024)
            frames, size = [], 0
            iterator = stream.__aiter__()
            async for raw in iterator:
                size += len(raw)
                if size > 2 * 1024 * 1024:
                    raise ValueError("Probe output limit")
                frames.extend(decoder.feed(raw))
                if "[DONE]" in frames:
                    decoder.finish()
                    await stream.complete()
                    completed = True
                    break
            signals, text = inspect_frames(frames)
            status, detail = classify(feature, signals, text)
            row.update(status=status, detail=detail, signals=signals)
    except JobFailed as exc:
        row.update(
            status="unavailable"
            if exc.job.failure
            and exc.job.failure.kind
            in {
                "no_capacity",
                "no_offering",
                "unreachable",
                "payment",
                "not_provisioned",
                "timeout",
            }
            else "inconclusive",
            detail="Engine job failed",
            job_id=str(exc.job.id),
            failure_kind=exc.job.failure.kind if exc.job.failure else "unknown",
        )
    except TimeoutError:
        row.update(status="unavailable", detail="Probe deadline exceeded")
    except Exception as exc:
        row.update(status="inconclusive", detail="Probe failed", exception_type=type(exc).__name__)
    finally:
        with anyio.CancelScope(shield=True):
            try:
                if stream is not None and not completed:
                    await stream.disconnected()
            finally:
                try:
                    if stream is not None:
                        await stream.aclose()
                finally:
                    if iterator is not None:
                        await iterator.aclose()
    return row


async def run(options):
    logging.disable(logging.CRITICAL)
    report = {
        "date": datetime.now(UTC).isoformat(),
        "engine_version": importlib.metadata.version("livepeer-builder"),
        "scope": "runner wire probes, not end-to-end gateway certification",
        "results": [],
    }
    # Never open the production database with a second engine.
    directory = Path(tempfile.mkdtemp(prefix="gateway-capability-probes-"))
    os.environ["LPB_DATABASE_URL"] = "sqlite+aiosqlite:///" + str(directory / "engine.db")
    os.environ["LPB_ENGINE_ID"] = "capability-probe-" + uuid4().hex
    report["evidence_database"] = str(directory / "engine.db")
    engine = None
    try:
        settings = load_livepeer_settings(Settings(backend="livepeer"))
        route = next(r for r in settings.routes if options.model is None or r.name == options.model)
        report.update(model=route.name, app=route.app)
        engine = await build_engine(settings)
        await engine.start()
        selected = cases()
        if options.feature:
            selected = {"baseline": {}, **{k: selected[k] for k in options.feature}}
        if options.baseline_report:
            previous = json.loads(await asyncio.to_thread(Path(options.baseline_report).read_text))
            if (
                previous.get("model") != route.name
                or previous.get("app") != route.app
                or not any(
                    r["feature"] == "baseline" and r["status"] == "observed"
                    for r in previous["results"]
                )
            ):
                raise ValueError("Baseline report does not establish this route")
            age = datetime.now(UTC) - datetime.fromisoformat(previous["date"])
            if age.total_seconds() < 0 or age.total_seconds() > 3600:
                raise ValueError("Baseline report is stale")
            report["baseline_evidence"] = previous
            selected.pop("baseline", None)
        for feature, changes in selected.items():
            row = await probe(engine, settings, route, feature, changes)
            report["results"].append(row)
            # Persist after every paid attempt, even if the process is later interrupted.
            await asyncio.to_thread(
                Path(options.output).write_text, json.dumps(report, indent=2) + "\n"
            )
            print(json.dumps(row), flush=True)
            if feature == "baseline" and row["status"] != "observed":
                report["results"].extend(
                    {
                        "feature": k,
                        "status": "unverified",
                        "detail": "Baseline failed; no feature request dispatched",
                    }
                    for k in selected
                    if k != "baseline"
                )
                break
    except Exception as exc:
        report["setup"] = {"status": "unavailable", "exception_type": type(exc).__name__}
    finally:
        if engine is not None:
            try:
                await engine.stop()
            except Exception as exc:
                report["cleanup"] = {"status": "failed", "exception_type": type(exc).__name__}
    await asyncio.to_thread(Path(options.output).write_text, json.dumps(report, indent=2) + "\n")
    print(json.dumps({"report": options.output, "report_data": report}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Dispatch paid probes")
    parser.add_argument("--model")
    parser.add_argument(
        "--baseline-report", help="Reuse successful same-route baseline from the last hour"
    )
    parser.add_argument("--feature", choices=list(cases()), action="append")
    parser.add_argument("--output", default="capability-probes.json")
    options = parser.parse_args()
    if not options.run:
        parser.error("Use --run to dispatch paid probes")
    asyncio.run(run(options))
