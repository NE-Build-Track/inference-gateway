# Livepeer backend

`GATEWAY_BACKEND=livepeer` embeds Builder Core in this process. The gateway translates
OpenAI Chat Completions and Anthropic Messages into a runner's OpenAI chat contract.
Both interfaces therefore serve the configured Livepeer model; an Anthropic interface
does not imply that the model is Claude.

Builder Core is pinned to `2134e29e9fb4d06790c23fea9311945b01e9fb01` (`releases/m3`, with native HTTP).
Core pins the Python gateway SDK to upstream `2017a80`, recorded in `uv.lock`; no fork is pinned.
Only the backend and application composition import Livepeer packages.

## Configuration and ownership

Local development defaults to `fake`. Compose defaults to `livepeer`. The application
loads settings at startup, creates the engine, validates streaming capability, starts
it, and closes it through FastAPI lifespan. Startup failure also closes acquired resources.
An explicitly injected backend is borrowed; its creator remains responsible for its engine.

The deployed engine requires `LPB_ALLOCATION_MODE=per_application`, `LPB_SIGNER_URL` (payments only),
`LPB_DISCOVERY_URL` (runners, from builder-core's discovery service; the signer is not a discovery
source, builder-core D43), `LPB_BATTERIES_URL`, `LPB_BATTERIES_TOKEN`, `LPB_SHARED_PAYMENT_KEY`,
`LPB_SHARED_ALLOCATION_ID`, and `LPB_SECRET_KEY`. Settings errors omit secret values.
`LPB_MAX_PAID_ATTEMPTS` (default 2, Compose sets it explicitly) bounds the paid attempts a job may
fail over from; `1` turns paid failover off and keeps unpaid failover.
The engine database must be durable; in-memory database URLs are rejected by the live
composition root. Engine initialization applies its migrations by default. Run one engine
process per database and keep the database volume across restarts.

| Gateway setting | Default | Meaning |
| --- | --- | --- |
| `GATEWAY_BACKEND` | `fake` locally; `livepeer` in Compose | Explicit backend selection; no silent fallback |
| `GATEWAY_APPLICATION_ID` | `endpoint-gateway` | Server-controlled engine application identity |
| `GATEWAY_ACTOR_ID` | `shared-application` | One server-controlled backend actor; gateway users are attributed in its own ledger |
| `GATEWAY_MODEL_NAME` | `qwen3.8-27b` | Model name clients send |
| `GATEWAY_CHAT_APP` | `blueclaw/chat/qwen3.8-27b` | Runner app selected by discovery |
| `GATEWAY_UPSTREAM_MODEL` | Omitted | Legacy explicit name; must equal route name and metadata `model_name` |
| `GATEWAY_MODEL_ROUTES` | Unset | JSON route array overriding the single-model settings |
| `GATEWAY_APPROVED_ORCHESTRATORS` | Empty | Comma-separated HTTP(S) origins; empty trusts the configured discovery source |
| `GATEWAY_TIMEOUT_SECONDS` | `300` | Total generation limit; live backend maximum 3600 seconds |
| `GATEWAY_FIRST_BYTE_TIMEOUT_SECONDS` | `60` | Engine opening/first-byte budget, capped by total |
| `GATEWAY_IDLE_TIMEOUT_SECONDS` | `60` | Maximum upstream idle interval |
| `GATEWAY_MAX_ATTEMPTS` | `3` | Runners Builder Core may try for one job, maximum 10; paid ones are also capped by `LPB_MAX_PAID_ATTEMPTS` |
| `GATEWAY_MAX_EVENT_BYTES` | `1048576` | Maximum buffered SSE frame bytes |
| `GATEWAY_MAX_OUTPUT_BYTES` | `8388608` | Output text and tool data limit per generation |

For multiple routes, configure:

```sh
GATEWAY_MODEL_ROUTES='[{"name":"qwen3.8-27b","app":"blueclaw/chat/qwen3.8-27b","disable_thinking":true},{"name":"served-name","app":"team/other-chat","upstream_model":"served-name","disable_thinking":false}]'
```

Unknown model names fail with 400 before engine dispatch. Routes must have unique model/app pairs, and names must match JSON metadata `model_name`.
Missing metadata excludes runners. See [model routing and Responses](../product-specs/models-and-responses.md)
for capacity selection, the injected selection policy, and native Responses transport.
Discovery is filtered to configured apps and, when supplied, approved origins including
scheme and port. The default Qwen route sends `chat_template_kwargs.enable_thinking=false`.
Routes for other runners can omit that extension with `disable_thinking=false`.

## Request and response path

The backend borrows the public `ActorApi`. Each generation becomes one `JobRequest`
to `/v1/chat/completions`, with a server-generated request ID as `operation_ref`.
That ID joins the public request header to the engine job in gateway logs. It is not
client idempotency: another HTTP request receives another operation reference.

Messages, system/developer instructions, tool definitions, selection, temperature, and
output limit are forwarded. Anthropic tool results become individual OpenAI tool messages;
`is_error` is represented by a `Tool error: ` prefix because that runner format has no
matching boolean field. Tool execution remains with the caller.

The backend always requests SSE and token usage, including for nonstream clients. It
parses fragmented UTF-8, CR/LF variants, multiline SSE data, content, and function arguments.
The first tool call streams its arguments as they arrive. On explicitly enabled/requested
parallel routes, every identified call streams independently. Otherwise later interleaved calls
buffer within the output limit and emit sequentially when the upstream terminates. At most 128
tool calls are accepted. Text arriving after tool output is currently rejected.

A successful response requires a supported finish reason, usage, a valid `[DONE]` marker,
and complete JSON-object tool arguments. It calls `JobStream.complete()` at the terminal
marker, so a server that holds its HTTP connection open cannot keep the paid stream alive.
It does not drain arbitrary trailing traffic after that marker. Unexpected JSON responses,
malformed chunks, missing usage/termination, unenabled or out-of-order reasoning content, invalid arguments, and
limit breaches fail explicitly. The adapter does not invent token counts or repair tool JSON.

The engine records successful byte delivery independently from gateway protocol validation.
For example, a runner that closes cleanly with malformed JSON may have an engine job marked
succeeded while the gateway returns an error. Gateway logs and HTTP/SSE errors describe that
application-level failure; the gateway does not rewrite the engine's financial evidence.

## Cancellation, errors, and usage

The HTTP boundary observes disconnects while opening, streaming, and collecting nonstream
responses. Once a `JobStream` exists, cancellation or generator closure invokes
`disconnected()` before closing its iterator. Builder Core applies its bounded settlement
window, then records the outcome and releases resources. A paid job can end `uncertain`;
a runner finishing during that window can end `succeeded`. The gateway preserves that result.
Before a `JobStream` is returned, cancellation propagates to the engine's opening path;
its recorded failure can be `runner_error` with an uncertain paid outcome.

The network tests use file-backed SQLite, matching deployment. An interrupted SQL operation
can invalidate the single connection of an in-memory SQLite database and erase that database;
in-memory engines are suitable only for controlled offline tests, not this runtime.

Only Builder Core retries. It fails a job over to another runner when the runner refused it
(503, 404, 409, or a refused payment), also after payment, but then only to another orchestrator
and at most `LPB_MAX_PAID_ATTEMPTS` times (Core D52, D55). The gateway never retries a paid or
partial job itself. Pre-header errors map to 400 for unknown models, 409 for operation conflicts,
503 for unavailable capacity/payment infrastructure, 504 for timeouts, or 502 for other backend
failures. A job Builder Core leaves `uncertain` (it may have run and been charged) is a 502, never a
503: a 503 says nothing ran, so a retry is safe. Native routes follow the same rule. After headers commit, an error frame ends the response without a success terminator.
Underlying messages and response bodies are not copied into public errors.

After completion, the gateway reports prompt, cached-input and completion counts to the engine.
A report failure preserves the completed reply and emits a correlated warning. Registry mode
persists measured usage and retries accounting reports durably. Network costs remain authoritative
in Builder Core; the gateway keeps dated reporting projections separately from its retail ledger.
See [accounting](accounting.md) for the distinct retail and wholesale contracts.

## Verification

Tests use the official SDKs, Builder Core's `FakeEngine`, and its real engine with a scripted
transport. They cover translation, byte fragmentation, interleaved tools, failure mapping,
engine-owned failover, usage, lifecycle, and real-socket disconnects with durable job records.
No funded signer or paid inference request is needed. See [deployment](../../deploy/README.md)
for the copied configuration, trust pins, and existing-stack network overlay.

HTTP errors include `x-should-retry: false`, which the tested OpenAI and Anthropic SDKs
honor. This prevents their default status-code retries from repeating a paid request.
Other clients must disable automatic retries themselves. Connection failures can still
trigger SDK retries; use `max_retries=0` when paid uncertainty must never cause a repeat.
Request IDs provide correlation, not cross-request idempotency or result replay.

Live runner compatibility: a completed tool call may arrive with `finish_reason: stop`.
After validating its identity and JSON-object arguments, the adapter maps it to the core
`tool_calls` outcome. A `length` finish or malformed arguments still fails validation.

Registry mode persists measured usage before settlement/reporting awaits and retries failed
accounting reports through the [accounting workflow](accounting.md). It still uses one upstream
application allocation. No accounting retry dispatches a new job.


## Extended capability status

Routes may opt into `structured_output`, `reasoning`, `stop`, and `parallel_tools` using their
`capabilities` array. It defaults to empty. The [extended contract](../product-specs/extended-generation.md#livepeer-route-capabilities)
defines the precise supported controls, signatures, stop evidence, and streaming behavior.
Requirements are checked against the selected route before paid dispatch, including history.
Other extended features remain guarded. Unexpected/invalid output returns `unsupported_response`
with the public message `Unsupported Response`; operational failures retain their classifications.

Reasoning on/off uses the Qwen/vLLM chat-template switch; effort and token budgets are not enabled.
The adapter accepts both reasoning delta names, emits empty Anthropic signatures, and forwards
unsigned reasoning history. Stop handling requests the marker, strips it across chunk boundaries,
and preserves actual runner usage. Structured output is independently checked by the core service
before a success finish. Parallel calls preserve identity while allowing incremental arguments.

[Route-specific evidence](../exec-plans/livepeer-capability-gaps.md) separates observed runner
support, adapter checks and live acceptance. Enable only the verified subset for the intended
runner. Public API behavior is tested through both SDKs; this does not claim native Claude semantics.

The request-field mappings follow vLLM's
[reasoning documentation](https://docs.vllm.ai/en/stable/features/reasoning_outputs/) and
[Chat Completions protocol](https://github.com/vllm-project/vllm/blob/main/vllm/entrypoints/openai/chat_completion/protocol.py).
Documentation describes possible runner features; the configured runner still needs acceptance tests.
