# Agent-compatible endpoint gateway

A Python gateway with OpenAI Chat Completions and Anthropic Messages interfaces.
Choose a deterministic fake or an embedded Livepeer Builder Core backend. Both interfaces support text,
streaming, tool calls, and tool-result follow-ups within the
[documented compatibility subset](docs/product-specs/compatibility.md).

## Run locally

Install Python 3.12 and [uv](https://docs.astral.sh/uv/), then run:

```sh
uv sync --locked
uv run python -m endpoint_gateway
```

The server binds to `127.0.0.1:8000`. Use `--port 8001` for a separate worktree.
No provider account or API key is needed. Set `GATEWAY_API_KEY` to require a local
shared key. Set `GATEWAY_TIMEOUT_SECONDS` to change the 30-second generation deadline.

```sh
curl http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"fake-model","messages":[{"role":"user","content":"hello"}]}'

curl -N http://127.0.0.1:8000/v1/messages \
  -H 'Content-Type: application/json' \
  -H 'anthropic-version: 2023-06-01' \
  -d '{"model":"fake-model","max_tokens":128,"stream":true,"messages":[{"role":"user","content":"hello"}]}'
```

Both produce `Echo: hello`, one as JSON and one as SSE. Any nonempty model name
selects the fake; it does not select a real provider model.

## Use an SDK

```python
from openai import OpenAI
from anthropic import Anthropic

openai = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="local")
answer = openai.chat.completions.create(
    model="fake-model", messages=[{"role": "user", "content": "hello"}]
)
print(answer.choices[0].message.content)

anthropic = Anthropic(base_url="http://127.0.0.1:8000", api_key="local")
with anthropic.messages.stream(
    model="fake-model", max_tokens=128,
    messages=[{"role": "user", "content": "hello"}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

If shared-key authentication is enabled, use its value as the SDK's `api_key`.
The fake requests the first tool offered unless tool choice disables it or the
latest message supplies results. It emits `{}` as arguments by default.
For schema-specific fixtures, inject `FakeBackend({"weather": {"city": "Boston"}})`
through `create_app`. The gateway never executes tools.

## Develop

```sh
make check
uv build
```

Checks cover formatting, lint, strict source typing, SDK contracts, failure paths,
real-socket streaming/disconnects, dependency boundaries, and documentation links.
The lockfile fixes dependency versions for local development and CI.

Start with [architecture](ARCHITECTURE.md), the [documentation index](docs/index.md),
or the [backend extension guide](docs/design-docs/backends.md).

## Deployment scaffold

The [deployment guide](deploy/README.md) describes the Compose setup copied from ChatLPT,
including an overlay that reuses its existing payment stack. Compose defaults to Livepeer;
local Python startup defaults to the fake. The [Livepeer guide](docs/design-docs/livepeer.md)
covers model routing, actor identity, engine lifecycle, usage, and cancellation.

With Livepeer selected, clients use `qwen3.8-27b` by default. Other names must be configured;
unknown names fail before dispatch. Both public API formats route to the same Livepeer model.
No Claude/OpenAI provider account is required. Payment services are never started implicitly
by the gateway-only Compose command.

Set `GATEWAY_ACCOUNTING_DB` to enable multiple users, CLI-issued API keys, versioned retail
pricing and usage/cost reporting. Compose enables this mode by default. See the
[accounting contract](docs/design-docs/accounting.md) and
[deployment guide](deploy/README.md) for provisioning and repeatable black-box checks.
