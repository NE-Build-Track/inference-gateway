# Initial compatibility contract

Status: implemented subset, verified against the SDK versions in `uv.lock`.
Compatibility means the listed request and response flows work with those SDKs.
It does not mean every provider feature or every agent application is supported.

## Interfaces

| Interface | Route | Version/header behavior |
| --- | --- | --- |
| OpenAI Chat Completions | `POST /v1/chat/completions` | Bearer key when local authentication is enabled |
| Anthropic Messages | `POST /v1/messages` | Requires `anthropic-version: 2023-06-01`; uses `x-api-key` when enabled |
| OpenAI Models | `GET /v1/models` | Bearer authentication; available approved model catalog |
| OpenAI Responses | `POST /v1/responses` | Stateless text/image/function subset; Bearer authentication |
| Liveness | `GET /healthz` | Unauthenticated; returns `{"status":"ok"}` |
| Readiness | `GET /readyz` | Backend/discovery readiness; 503 when unavailable |

OpenAI uses `/v1` in the SDK base URL; Anthropic uses the server root.
Both generation endpoints return `x-request-id` and `request-id` headers.
The fake makes no upstream calls. Livepeer mode dispatches paid jobs through Builder Core.

## Supported inputs

Both accept a nonempty model name and message list, `stream`, tool definitions,
tool choice, and temperature. OpenAI permits system/developer/user/assistant/tool
roles; Anthropic uses user/assistant roles and a top-level `system` value.
Text can be a string or a list of text blocks; extended media and reasoning shapes are described below. Tool history includes assistant calls
and client-supplied results referencing their IDs. IDs must be unique within history;
each result must refer to an outstanding call. All calls need results before another
generation. This scaffold validates references, not every provider-specific history rule.

| Feature | OpenAI | Anthropic |
| --- | --- | --- |
| Output limit | `max_tokens` or `max_completion_tokens`, defaults to 1024 | Required positive `max_tokens` |
| Tool definition | `type: function`, name, description, parameters | name, description, input_schema |
| Selection | `auto`, `none`, `required`, named function | `auto`, `none`, `any`, named tool |
| Tool input | JSON object encoded in `arguments` | Object in `input` |
| Tool result | `role: tool`, `tool_call_id`, text | `tool_result` block, text, optional `is_error` |
| Stream usage | `stream_options.include_usage` | Final message usage |
| Multiple completions | Only `n: 1` | One message |

Function definitions accept optional `strict:true` on OpenAI interfaces. The gateway validates
and enforces the supported parameter schema subset for those tools; omitted/false keeps existing
non-strict behavior. All generated arguments must encode JSON objects. Structured text and tool
schemas are independent and may be supplied together. See the
[Models/Responses contract](models-and-responses.md) for strict schema and streaming semantics.
The fake generates schema examples for strict tools and otherwise emits `{}` unless argument
fixtures are supplied. Anthropic tool definitions retain their existing non-strict behavior.
Nullable SDK fields `caller` and `toolset_name` are accepted on historical Anthropic
tool-use blocks only when null; their non-null features are unsupported.

Unknown fields, unsupported content types, malformed JSON, and invalid supported
fields return HTTP 400 with a protocol-specific error envelope. Unsupported fields
are never silently dropped. Optional fields generally should be omitted rather than
sent as null unless the request model explicitly allows null.

## Output and streaming

Nonstream responses contain provider-shaped assistant messages, finish reasons, and
usage. OpenAI streams completion chunks with content or indexed tool-call deltas,
a finish chunk, optional usage chunk, then `[DONE]`. Anthropic streams message start,
content block start/delta/stop, message delta, and message stop events. Tool JSON may
span several fragments. Backends may emit sequential text and multiple tool blocks;
the fake selects one tool unless parallel calls are explicitly enabled.

Prestream failures return HTTP 502; generation deadline expiry before response
commit returns 504. After headers commit, failures emit an error frame and close
without a success terminator. OpenAI midstream error framing is a gateway convention,
not a claim of universal provider behavior. Cancellation closes the backend generator.
The gateway never automatically retries a failed or partial generation.

## Fake semantics

- Any model name uses the same fake backend.
- Plain text yields `Echo: <latest message text>` in four-character fragments.
- Offered tools trigger the first tool, or the explicitly selected tool; `none` disables this.
- A latest message with tool results yields `Tool result: <result text>` in auto mode.
- Required or named choice can request another tool even after results.
- Call IDs are deterministic and advance with the number of historical calls.
- Input usage is zero; output usage counts characters. It is not token or billing data.
- Text limits count characters and yield `length`/`max_tokens` when truncated.
- Tool arguments are not truncated; temperature, descriptions, and schemas do not
  influence fake generation. They remain available to replacement backends.

## Extended generation and deferred endpoints

Reasoning, sampling controls, structured output, citations, hosted search, interleaved client
tools, and image/audio contracts are implemented in the gateway and deterministic fake.
See the [extended generation contract](extended-generation.md) for exact fields, limits,
examples and gateway extensions. Livepeer routes can opt into structured output, reasoning on/off,
stop controls and parallel tools. Other extended requests/history still fail before paid dispatch.

See [Models and Responses](models-and-responses.md) for their separate stateless contract.
Embeddings, stored conversations, and customer payment collection remain outside this slice. Registry mode provides persistent usage and retail/wholesale accounting. The server's generated OpenAPI page lists routes but does not yet describe the
manually parsed request bodies; this document and request models define the contract.

## Livepeer backend

Livepeer mode requires a configured model route; the default public name is `qwen3.8-27b`.
It preserves the interfaces above but uses real runner output and usage. It supports one
choice and a runner that streams OpenAI chat chunks with usage and `[DONE]`. Explicit parallel
requests on enabled routes stream independently indexed calls; other requests preserve sequential
normalization. Missing usage, malformed/truncated output, unsupported finish reasons, unenabled
reasoning content, or text after tools fail explicitly. No token counts are fabricated.

Engine capacity/payment failures map to 503, operation conflicts to 409, and deadlines to
504 before headers. Public errors omit upstream details. See the [Livepeer contract](../design-docs/livepeer.md)
for cancellation, tool-result translation, request correlation, and engine-owned accounting.

See [accounting and reporting](../design-docs/accounting.md) for API-key permissions,
provider-shaped reporting endpoints and their supported subsets.
