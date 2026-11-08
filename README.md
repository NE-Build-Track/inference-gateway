# Inference Gateway

[![Checks](https://github.com/NE-Build-Track/inference-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/NE-Build-Track/inference-gateway/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

OpenAI-, Anthropic- and System One-compatible APIs with pluggable backends, multi-user access,
and separate retail and wholesale accounting.

## Motivation

Agent applications should be able to change inference infrastructure without replacing
all their client integrations. Operators also need to know who used the service, what
they charged that user, and what the backend charged them. Those are different records.

Inference Gateway provides the OpenAI and Anthropic formats over a shared generation contract,
and forwards native requests, including System One decisions, to runners that speak them directly.
It ships with a deterministic fake for development and a Livepeer Builder Core adapter for paid
inference. Using the Anthropic interface does not imply that the underlying model is Claude;
each interface uses the configured backend and model route.

## Supported APIs and SDKs

| SDK | Base URL | Inference endpoints | Authentication | Live evidence |
| --- | --- | --- | --- | --- |
| OpenAI (`openai` 2.54.0) | `http://host/v1` | `POST /v1/chat/completions`, `POST /v1/responses` | `Authorization: Bearer` | Paid, on vLLM |
| Anthropic (`anthropic` 0.125.0) | `http://host` | `POST /v1/messages` (requires `anthropic-version`) | `x-api-key` | Paid, on vLLM |
| TypeSafe System One (`typesafe-sdk` 0.7.5) | `http://host` | `POST /v1/systemone` | `Authorization: Bearer` | Offline only |

The versions are the ones the test suite runs against, recorded in `uv.lock`. Each API supports a
[documented subset](docs/product-specs/compatibility.md) of its provider's features. System One is
for decision models such as Cloudflare Clef, which answer typed questions with probabilities and
generate no text. It works only on [native routes](docs/product-specs/system-one.md), so the fake
backend rejects it.

| Other endpoints | Purpose | Access |
| --- | --- | --- |
| `GET /v1/models` | Lists only the models the calling SDK can use, in its format; see [model lists](docs/product-specs/models-and-responses.md#model-lists) | Any inference key |
| `GET /healthz`, `GET /readyz` | Liveness and backend readiness | None |
| `GET /v1/organization/usage/completions`, `GET /v1/organization/costs` | OpenAI-shaped reports | Registry mode; see [accounting](docs/design-docs/accounting.md) |
| `GET /v1/organizations/usage_report/messages`, `GET /v1/organizations/cost_report` | Anthropic-shaped reports | Registry mode |
| `GET /v1/gateway/requests`, `/usage`, `/costs`, `/wholesale` | Gateway reports | Registry mode |

## Capabilities and limits

- Text, SSE streaming, tool calls and tool-result follow-ups through the OpenAI and Anthropic APIs.
- Decision models through the System One API, with text and inline images.
- Multiple users and scoped API keys, administered through a local CLI.
- Versioned retail rates, cached-input accounting, append-only charges and adjustments.
- Backend wholesale evidence, correlated jobs and durable accounting reconciliation.
- Provider-shaped usage/cost reports with user isolation and administrative reporting.

This is a documented compatibility subset. The gateway and deterministic fake also support
reasoning, additional sampling controls, structured output, citations, simulated hosted search,
interleaved tool calls and image/audio fixtures. See the [extended feature contract](docs/product-specs/extended-generation.md)
for native fields and gateway extensions. Livepeer routes can explicitly enable structured output,
reasoning on/off, stop controls and parallel tools. Image input has passed paid checks on a vLLM
route. Additional guarded adapter paths cover sampling, reasoning effort/budgets and audio input; they
remain unverified and disabled locally. Citations,
hosted search and media output have fake contracts only.
A [stateless Responses subset and per-SDK model lists](docs/product-specs/models-and-responses.md)
are also implemented. Livepeer routing reads [runner contract v1](docs/product-specs/runner-contract.md) envelopes only.
The gateway does not execute tools,
collect customer payments or replay generated responses. See the
[compatibility contract](docs/product-specs/compatibility.md).

## Native backend direction

Native routes support **vLLM, Ollama and SGLang** through original-body forwarding,
metadata-aware admission and read-only usage accounting. Enable them explicitly per model;
existing routes default to validated generation. Native strictness belongs to the backend.
[System One decisions](docs/product-specs/system-one.md) use this path, and an operator approves
each runner per endpoint.

The [native contract](docs/product-specs/native-inference.md) describes configuration and
limits. The [live-runner bundle](live-runner/README.md) supplies candidate manifests,
pinned images and readiness checks. The [Qwen3.8 acceptance report](live-runner/tests/qwen38-followup-2026-10-27.md)
records GPU tests on all three engines and four paid calls with reconciled wholesale costs.
Chain finality and production admission remain pending. Builder Core is pinned to NE-Build-Track
`releases/m3`; Node, Clearinghouse Batteries and the Python SDK come from `livepeer/`.

## Quick start

Install Python 3.12+ and [uv](https://docs.astral.sh/uv/), then:

```sh
git clone https://github.com/NE-Build-Track/inference-gateway.git
cd inference-gateway
uv sync --locked
uv run python -m inference_gateway
```

This starts the fake backend on `127.0.0.1:8000`. Local startup has no authentication
unless configured. No GPU, payer account or provider credentials are needed for this mode.
The Python package is `inference_gateway`; the installed CLI is `gateway`.

```sh
curl http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"fake-model","messages":[{"role":"user","content":"hello"}]}'
```

The fake returns `Echo: hello`. Its usage counts are synthetic and suitable only for tests.

## SDK clients

```python
from openai import OpenAI
from anthropic import Anthropic

openai = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="local", max_retries=0)
reply = openai.chat.completions.create(
    model="fake-model", messages=[{"role": "user", "content": "hello"}]
)
print(reply.choices[0].message.content)

anthropic = Anthropic(base_url="http://127.0.0.1:8000", api_key="local", max_retries=0)
with anthropic.messages.stream(
    model="fake-model", max_tokens=128,
    messages=[{"role": "user", "content": "hello"}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

Use a CLI-issued secret instead of `local` in registry mode. Configure the public model
name for live inference. SDK retries are disabled here because another HTTP request can
start another paid generation. For OpenAI streaming usage, pass
`stream_options={"include_usage": True}`.

Decision models need a native route and these TypeSafe settings. That SDK ignores the gateway's
`x-should-retry` header, and the gateway rejects its compressed bodies:

```python
from typesafe_sdk import Noul, RetryPolicy, TypeSafeClient

decisions = TypeSafeClient(
    base_url="https://your-gateway",
    api_key="your-gateway-key",
    model="Cloudflare/clef-flash",
    retry=RetryPolicy(max_retries=0),
    compression=False,
)
result = decisions.system_one(
    state="The parcel arrived damaged.",
    questions={"needs_help": Noul(instructions="Does the customer need help?")},
)
print(result.nouls["needs_help"].noul)
```

## Architecture

```mermaid
flowchart LR
    O[OpenAI clients] --> H[HTTP authentication and protocol adapters]
    A[Anthropic clients] --> H
    T[TypeSafe clients] --> H
    H --> G[Generation service]
    H --> V[Native forwarding]
    G --> F[Fake backend]
    G --> L[Livepeer adapter / Builder Core]
    V --> L
    H --> R[Gateway identity and retail ledger]
    L --> N[Orchestrator and inference runner]
    L --> W[Backend wholesale accounting]
    W --> P[Reconciled reporting projections]
    R --> Q[Authorized reporting APIs]
    P --> Q
```

One gateway process owns a durable SQLite identity/retail database. Builder Core owns
its separate job/payment database. Retail charges use configured price versions;
wholesale costs retain backend evidence and uncertainty. Reconciliation never retries
inference. Validated routes translate requests through the generation service; native routes
forward the original bytes and only read usage. See [architecture](ARCHITECTURE.md) and
[accounting](docs/design-docs/accounting.md).

## Configuration

| Setting | Purpose |
| --- | --- |
| `GATEWAY_BACKEND` | `fake` locally; `livepeer` in deployment |
| `GATEWAY_ACCOUNTING_DB` | Enables registry authentication and durable retail accounting |
| `GATEWAY_API_KEY` | Optional legacy shared key; cannot be combined with registry mode |
| `GATEWAY_MODEL_ROUTES` | Public model names, each with the capability its runners serve (`chat` by default), `mode` and optional list details |
| `GATEWAY_APPROVALS_FILE` | Approvals for native routes and private runners, each with a passing conformance report; re-read when it changes |
| `GATEWAY_MODEL_NAME` | Single-route setting used when `GATEWAY_MODEL_ROUTES` is empty |
| `GATEWAY_TIMEOUT_SECONDS` | Generation deadline; defaults to 300 seconds |
| `GATEWAY_APPROVED_ORCHESTRATORS` | Allowed discovery origins for Livepeer |
| `LPB_DISCOVERY_URL` | Required runner inventory endpoint; separate from the signer |
| `LPB_*` | Builder Core configuration and upstream payment credentials |

Registry mode fails closed until keys and prices are provisioned. For example:

```sh
export GATEWAY_ACCOUNTING_DB="$PWD/gateway.db"
uv run gateway users create alice
# Use the returned USER_ID. The following command prints the new secret once.
uv run gateway keys create USER_ID --name development --scopes inference,reports
uv run gateway prices set --model fake-model --version development-v1 \
  --input-per-million 0 --output-per-million 0
uv run python -m inference_gateway
```

The CLI also supports revocation, rotation, expiry, user disabling, adjustments and
reconciliation redrive. See [operations](docs/OPERATIONS.md) and the
[configuration/accounting reference](docs/design-docs/accounting.md).

## Build and deployment

```sh
make check
uv build
scripts/build-images.sh gateway              # inference-gateway:local
IMAGE_TAG=v0.3.0 scripts/build-images.sh     # All three images under one release tag
```

The script builds locally and never publishes.

Two independent Compose deployments live under [deploy/livepeer](deploy/livepeer/README.md):

- **Gateway:** API service, embedded Builder Core, discovery, Clearinghouse and payment signer.
- **Orchestrator:** `livepeer/node` and local vLLM, with explicit off-chain/on-chain configuration.

Each deployment has its own configuration, secrets and persistent volumes. No sibling
application checkout is required. Livepeer dependencies build from pinned upstream commits;
model weights, GPU drivers, operator credentials, RPC access and funding remain external
prerequisites. A GPU is required for the supplied vLLM configuration. Neither funding nor
image publication happens implicitly.

## Development conventions

Keep the core framework-independent. Inject dependencies explicitly, translate wire formats
at adapters, preserve cancellation, and keep accounting retries separate from generation.
Never log prompts, tool arguments, credentials or raw upstream error bodies. Add behavior
and compatibility tests when contracts change; update the relevant documentation with them.

`make check` runs lint, formatting, strict typing, offline tests and documentation checks.
It includes a separate-process HTTP black-box suite. Paid acceptance is opt-in through
`make test-live` and `make test-accounting-live`; see the deployment guide for credentials.
Verification results and limits are recorded in [quality status](docs/QUALITY.md).

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), follow the
[Code of Conduct](CODE_OF_CONDUCT.md), and use the issue/PR templates. Security issues
should follow [SECURITY.md](SECURITY.md). The project is licensed under [MIT](LICENSE).

Related repository upgrades must preserve native transport. The
[28 October compatibility review](docs/references/repository-compatibility-2026-10-28.md)
found that native transport had to move into Core after M3 moved the paid call there. Core PR
#55 did that and merged as `2134e29`. The gateway pins the M3 merge, `releases/m3` commit
`1514212` (2 November), which includes it; Core pins the upstream Python SDK, and deployment images build
Node and Clearinghouse Batteries from `livepeer/` `main`. The
[29 October repin evidence](live-runner/tests/m3-upstream-2026-10-29.md) records the tested
combination. Use the locked dependencies (`uv sync --locked`).
