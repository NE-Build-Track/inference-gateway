"""Opt-in SDK acceptance against an already configured gateway. Saves no generated content."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from openai import AsyncOpenAI


async def run(options):
    report = {"date": datetime.now(UTC).isoformat(), "model": options.model, "results": []}
    key = (await asyncio.to_thread(Path(options.key_file).read_text)).strip()
    async with AsyncOpenAI(
        api_key=key, base_url=options.url.rstrip("/") + "/v1", max_retries=0, timeout=120
    ) as client:
        try:
            catalog = await client.models.list()
            assert options.model in {model.id for model in catalog.data}
            report["catalog"] = "passed"
        except Exception as exc:
            report["catalog"] = {"status": "failed", "exception_type": type(exc).__name__}
            await asyncio.to_thread(
                Path(options.output).write_text, json.dumps(report, indent=2) + "\n"
            )
            return
        for streaming in (False, True):
            row = {"streaming": streaming}
            try:
                result = await client.responses.create(
                    model=options.model,
                    input="What is 17 times 23? Answer only the number.",
                    max_output_tokens=256,
                    store=False,
                    stream=streaming,
                )
                if streaming:
                    events = [e async for e in result]
                    assert events and events[-1].type == "response.completed"
                    result = events[-1].response
                assert result.status == "completed" and result.output_text.strip() == "391"
                assert (
                    result.usage
                    and result.usage.total_tokens
                    == result.usage.input_tokens + result.usage.output_tokens
                )
                row.update(
                    status="passed",
                    response_id=result.id,
                    input_tokens=result.usage.input_tokens,
                    output_tokens=result.usage.output_tokens,
                )
            except Exception as exc:
                row.update(status="failed", exception_type=type(exc).__name__)
            report["results"].append(row)
            await asyncio.to_thread(
                Path(options.output).write_text, json.dumps(report, indent=2) + "\n"
            )
            print(json.dumps(row), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--model", required=True)
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--output", default="responses-acceptance.json")
    args = parser.parse_args()
    if not args.run:
        parser.error("--run is required: requests can incur inference charges")
    asyncio.run(run(args))
