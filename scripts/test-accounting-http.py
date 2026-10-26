"""Black-box acceptance: only public HTTP, supplied keys, and provider wire formats.

Use dedicated test users/keys. This issues 14 inference requests. With a live backend
these are paid requests. No server imports, database reads, or credential provisioning.
"""

import asyncio
import json
import os
import time
from decimal import ROUND_HALF_EVEN, Decimal

import httpx


async def generate(client, key, model, protocol, streaming, tools=False, followup=None):
    path = "/v1/chat/completions" if protocol == "openai" else "/v1/messages"
    headers = (
        {"Authorization": "Bearer " + key}
        if protocol == "openai"
        else {"x-api-key": key, "anthropic-version": "2023-06-01"}
    )
    messages = [{"role": "user", "content": "Say hello in one short sentence."}]
    body = {
        "model": model,
        "messages": messages,
        "stream": streaming,
        "max_tokens": 128,
        "temperature": 0,
    }
    if tools:
        messages[0]["content"] = "Call lookup with an empty JSON object now."
        schema = {"type": "object", "properties": {}, "additionalProperties": False}
        if protocol == "openai":
            body["tools"] = [
                {"type": "function", "function": {"name": "lookup", "parameters": schema}}
            ]
            body["tool_choice"] = {"type": "function", "function": {"name": "lookup"}}
        else:
            body["tools"] = [{"name": "lookup", "input_schema": schema}]
            body["tool_choice"] = {"type": "tool", "name": "lookup"}
    if followup:
        messages.append(followup)
        if protocol == "openai":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": followup["tool_calls"][0]["id"],
                    "content": "The number is 42.",
                }
            )
            body["tool_choice"] = "none"
        else:
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": followup["content"][0]["id"],
                            "content": "The number is 42.",
                        }
                    ],
                }
            )
            body["tool_choice"] = {"type": "none"}
    if streaming and protocol == "openai":
        body["stream_options"] = {"include_usage": True}
    usage = {}
    text = ""
    call = {"id": "", "name": "", "arguments": ""}
    finish = None
    terminal = False
    async with client.stream("POST", path, json=body, headers=headers) as response:
        assert response.status_code == 200, f"inference status={response.status_code}"
        request_id = response.headers["x-request-id"]
        if not streaming:
            payload = json.loads(await response.aread())
            usage = payload["usage"]
            if protocol == "openai":
                choice = payload["choices"][0]
                finish = choice["finish_reason"]
                text = choice["message"].get("content") or ""
                for item in choice["message"].get("tool_calls") or []:
                    call = {
                        "id": item["id"],
                        "name": item["function"]["name"],
                        "arguments": item["function"]["arguments"],
                    }
            else:
                finish = payload["stop_reason"]
                for item in payload["content"]:
                    if item["type"] == "text":
                        text += item["text"]
                    else:
                        call = {
                            "id": item["id"],
                            "name": item["name"],
                            "arguments": json.dumps(item["input"]),
                        }
        else:
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    terminal = True
                    continue
                payload = json.loads(data)
                assert "error" not in payload, "stream returned an error"
                if protocol == "openai":
                    usage = payload.get("usage") or usage
                    for choice in payload["choices"]:
                        finish = choice.get("finish_reason") or finish
                        delta = choice["delta"]
                        text += delta.get("content") or ""
                        for item in delta.get("tool_calls") or []:
                            call["id"] += item.get("id") or ""
                            function = item.get("function") or {}
                            call["name"] += function.get("name") or ""
                            call["arguments"] += function.get("arguments") or ""
                else:
                    kind = payload["type"]
                    if kind == "message_start":
                        usage.update(payload["message"]["usage"])
                    elif (
                        kind == "content_block_start"
                        and payload["content_block"]["type"] == "tool_use"
                    ):
                        call.update(
                            id=payload["content_block"]["id"], name=payload["content_block"]["name"]
                        )
                    elif kind == "content_block_delta":
                        text += payload["delta"].get("text", "")
                        call["arguments"] += payload["delta"].get("partial_json", "")
                    elif kind == "message_delta":
                        usage.update(payload["usage"])
                        finish = payload["delta"]["stop_reason"]
                    elif kind == "message_stop":
                        terminal = True
            assert terminal, "missing stream termination"
    assistant = None
    if tools and not followup:
        assert finish in {"tool_calls", "tool_use"} and call["id"] and call["name"] == "lookup"
        assert json.loads(call["arguments"]) == {}
        assistant = (
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {"name": call["name"], "arguments": call["arguments"]},
                    }
                ],
            }
            if protocol == "openai"
            else {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": call["id"], "name": call["name"], "input": {}}
                ],
            }
        )
    else:
        assert finish in {"stop", "end_turn"} and text.strip()
        if followup:
            assert "42" in text
    cached = (
        (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
        if protocol == "openai"
        else usage.get("cache_read_input_tokens", 0)
    )
    inputs = usage["prompt_tokens"] if protocol == "openai" else usage["input_tokens"] + cached
    outputs = usage["completion_tokens"] if protocol == "openai" else usage["output_tokens"]
    return request_id, (inputs, outputs, cached), assistant


async def run():
    base = os.getenv("GATEWAY_TEST_URL", "http://127.0.0.1:8000")
    model = os.getenv("GATEWAY_MODEL_NAME", "qwen3.8-27b")
    keys = {k: os.environ["GATEWAY_TEST_KEY_" + k] for k in ["A", "A2", "B", "ADMIN"]}
    rates = [
        Decimal(os.environ["GATEWAY_TEST_" + name])
        for name in ["INPUT_RATE", "OUTPUT_RATE", "CACHED_RATE"]
    ]
    start = int(time.time()) - 1
    expected = {}
    async with httpx.AsyncClient(base_url=base, timeout=310) as client:
        for protocol in ["openai", "anthropic"]:
            for stream in [False, True]:
                for tool in [False, True]:
                    rid, counts, assistant = await generate(
                        client, keys["A"], model, protocol, stream, tool
                    )
                    expected[rid] = counts
                    print(
                        f"PASS {protocol}/{'tool' if tool else 'text'}/"
                        f"{'stream' if stream else 'json'}",
                        flush=True,
                    )
                    if assistant:
                        rid, counts, _ = await generate(
                            client, keys["A"], model, protocol, stream, True, assistant
                        )
                        expected[rid] = counts
                        print(
                            f"PASS {protocol}/tool-result/{'stream' if stream else 'json'}",
                            flush=True,
                        )
        rid, counts, _ = await generate(client, keys["A2"], model, "openai", False)
        expected[rid] = counts
        bob, _, _ = await generate(client, keys["B"], model, "anthropic", False)
        params = {"start_time": start, "limit": 1000}

        async def rows(key):
            result = await client.get(
                "/v1/gateway/requests", params=params, headers={"x-api-key": key}
            )
            assert result.status_code == 200
            return {r["id"]: r for r in result.json()["data"]}

        alice = await rows(keys["A"])
        assert set(expected) <= set(alice) and bob not in alice
        assert set(await rows(keys["A2"])) == set(alice)
        bobs = await rows(keys["B"])
        assert bob in bobs and not set(expected) & set(bobs)
        for rid, counts in expected.items():
            row = alice[rid]
            assert (row["input_tokens"], row["output_tokens"], row["cached_input_tokens"]) == counts
            inputs, outputs, cached = counts
            amount = (
                (inputs - cached) * rates[0] + outputs * rates[1] + cached * rates[2]
            ) / Decimal(1000000)
            assert Decimal(row["retail_amount"]) == amount.quantize(
                Decimal("0.000000001"), rounding=ROUND_HALF_EVEN
            )
            assert len(row["entries"]) == 1 and row["outcome"] == "succeeded"
            assert "wholesale_amount" not in row
        assert len({alice[r]["key_id"] for r in expected}) == 2
        # Provider-style grouped reports must agree with each other and the request ledger.
        akeys = sorted({alice[r]["key_id"] for r in expected})
        filters = [("api_key_ids[]", key) for key in akeys]
        oa = await client.get(
            "/v1/organization/usage/completions",
            params=[("start_time", start), ("group_by[]", "api_key_id"), *filters],
            headers={"Authorization": "Bearer " + keys["A"]},
        )
        an = await client.get(
            "/v1/organizations/usage_report/messages",
            params=[("starting_at", start), ("group_by[]", "api_key_id"), *filters],
            headers={"x-api-key": keys["A"]},
        )
        assert oa.status_code == an.status_code == 200
        oat = sum(r["input_tokens"] for b in oa.json()["data"] for r in b["results"])
        ant = sum(
            r["uncached_input_tokens"] + r["cache_read_input_tokens"]
            for b in an.json()["data"]
            for r in b["results"]
        )
        assert oat == ant == sum(r["input_tokens"] for r in alice.values())
        oc = await client.get(
            "/v1/organization/costs", params=params, headers={"x-api-key": keys["A"]}
        )
        ac = await client.get(
            "/v1/organizations/cost_report",
            params={"starting_at": start},
            headers={"x-api-key": keys["A"]},
        )
        total = sum((Decimal(r["retail_amount"]) for r in alice.values()), Decimal(0))
        assert (
            sum(
                (
                    Decimal(str(r["amount"]["value"]))
                    for b in oc.json()["data"]
                    for r in b["results"]
                ),
                Decimal(0),
            )
            == total
        )
        assert (
            sum(
                (Decimal(r["amount"]) / 100 for b in ac.json()["data"] for r in b["results"]),
                Decimal(0),
            )
            == total
        )
        denied = await client.get(
            "/v1/gateway/wholesale", params=params, headers={"x-api-key": keys["A"]}
        )
        assert denied.status_code == 403
        hidden = await client.get(
            "/v1/gateway/requests",
            params={**params, "user_ids": next(iter(alice.values()))["user_id"]},
            headers={"x-api-key": keys["B"]},
        )
        assert hidden.json()["data"] == []
        print(
            "PASS retail arithmetic, usage reports, attribution and isolation",
            flush=True,
        )
        deadline = time.monotonic() + int(os.getenv("GATEWAY_TEST_RECONCILE_TIMEOUT", "180"))
        while True:
            admin = await rows(keys["ADMIN"])
            selected = [admin[r] for r in expected]
            if all(r["wholesale_status"] in {"none", "observed", "corrected"} for r in selected):
                assert all(r["wholesale_amount"] is not None for r in selected)
                assert all(r["job_id"] for r in selected)
                for row in selected:
                    if row["attempts"] and all(
                        a["amount_usd"] is not None for a in row["attempts"]
                    ):
                        assert Decimal(row["wholesale_amount"]) == sum(
                            (Decimal(a["amount_usd"]) for a in row["attempts"]), Decimal(0)
                        )
                    if row["wholesale_usage_source"] == "app_reported":
                        assert row["wholesale_usage"]["prompt_tokens"] == row["input_tokens"]
                        assert row["wholesale_usage"]["completion_tokens"] == row["output_tokens"]
                    if os.getenv("GATEWAY_TEST_EXPECT_PAID") == "1":
                        assert row["wholesale_status"] in {"observed", "corrected"}
                        assert Decimal(row["wholesale_amount"]) > 0
                print(
                    "PASS wholesale evidence and job correlation",
                    flush=True,
                )
                break
            if time.monotonic() >= deadline:
                counts = {
                    s: sum(r["wholesale_status"] == s for r in selected)
                    for s in {r["wholesale_status"] for r in selected}
                }
                raise AssertionError("Wholesale reconciliation unresolved: " + json.dumps(counts))
            await asyncio.sleep(2)
    print("All black-box accounting checks passed", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception as error:
        # Test credentials and response bodies must never appear in diagnostics.
        print("FAIL accounting black-box suite: " + type(error).__name__, flush=True)
        raise SystemExit(1) from None
