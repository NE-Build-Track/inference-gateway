# Native inference forwarding and runner contracts

Status: accepted architectural direction, 2026-10-27. Native forwarding is implemented behind explicit route configuration and
operator admission, with offline acceptance. The [live-runner bundle](../../live-runner/README.md) implements an offline-tested
candidate deployment/metadata contract. Its schemas supersede illustrative metadata
below and pin image digests rather than unmeasured runtime versions. Routes still default to validated adapters. See the [native contract](../product-specs/native-inference.md)
for the implemented configuration and remaining live acceptance.

## Decision and supported backend families

The supported backend families for the native inference direction are **vLLM,
Ollama, and SGLang**. Support attaches to a tested server image, loaded model,
configuration and endpoint profile, not every release or every model in a family.
The fake remains a development/test backend. Livepeer remains the transport and
payment layer; it is not a fourth LLM runtime.

Prefer forwarding a client request to the same native endpoint on an eligible
runner. Keep request bodies and response/SSE payloads unchanged wherever the declared
contract permits it. The runtime owns prompt templates, generation parameters,
tool parsing, schema enforcement and its protocol-specific extensions. Do not make
the gateway implement each new runtime field to let that field reach the runtime.

Retain translated/validated generation as an explicit route mode for clients that
need conversion or gateway-enforced output guarantees. Do not silently change the
existing strict validation promise when introducing native forwarding. Native mode
has backend-enforced strictness, not gateway-enforced schema correctness.

Current upstream documentation describes Chat Completions, Responses and Anthropic
Messages in these server families. Their feature coverage and configuration differ:

- [vLLM serving APIs](https://docs.vllm.ai/en/latest/serving/online_serving/).
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility) and
  [Anthropic compatibility](https://docs.ollama.com/api/anthropic-compatibility).
- [SGLang Chat Completions](https://docs.sglang.io/docs/basic_usage/openai_api_completions),
  [Messages](https://docs.sglang.io/docs/basic_usage/anthropic_api) and
  [Responses release notes](https://www.sglang.io/).

These sources were reviewed on 2026-10-27. They do not certify old pinned images,
our deployment, full provider parity, or universal strict-schema support.

## Review of the runner example

Reviewed `livepeer-runner-app-examples` at `a526b43` and `livepeer-node` at `dfcf033`.
The examples use the older go-livepeer deployment conventions; their configuration
cannot simply be pasted into livepeer-node's TOML configuration.

The vLLM example is the right **transport pattern**, but not a sufficient production
contract. Stock vLLM serves HTTP; the orchestrator statically registers it, polls
health and proxies traffic. The runner needs no model-specific Livepeer SDK code.

| Inspected example | Finding | Required change |
| --- | --- | --- |
| `vllm/runners.json` | No metadata; model is encoded in app name | Publish explicit model identity and a versioned native-endpoint contract |
| `vllm/compose.yml` | Uses `vllm/vllm-openai:latest` | Pin an image/digest and record configuration/model identity |
| `vllm/gateway.py` | Selects a fixed app and takes its first candidate | Use model, endpoint, contract, trust, capacity and health eligibility |
| Same demo gateway | Parses/re-encodes JSON; uses `request.path` without query; does not copy client protocol headers | Preserve bounded original body, supported query and end-to-end protocol headers |
| Same demo gateway | Unary path returns `web.json_response(result.data)` | Preserve upstream status, content type and body; retain allowed response headers |
| Same demo gateway | Wildcard `/v1` routing; model list can become a paid runner call | Allow only approved public inference operations; aggregate/cache catalog separately |
| Same demo gateway | No retail ledger or usage observation | Integrate gateway accounting and paid-job correlation |
| Example coverage | Demonstrates chat/SSE, not all three runtimes and endpoints | Add pinned runtime configurations and protocol/accounting conformance cases |
| Root README | Says selection ignores metadata and selection dimensions belong in app IDs | Update the example guidance for a metadata-aware consumer; keep node protocol generic |

The API-proxy example pins hosted image-model paths and injects an operator credential.
It is not a general LLM runtime wrapper. Its health check reports nginx liveness,
not upstream model readiness; copying that probe would not prove LLM eligibility.

The current inference-gateway also rejects the vLLM example's unmodified registration:
it requires `metadata.model_name`. The example has none.

## What livepeer-node already provides

The inspected node has opaque `Metadata` on static runner configuration and dynamic
heartbeats and emits it in discovery. It provides mode, observed time, available
capacity, version, GPU and price fields separately. Usability checks include ready
status, health, and heartbeat freshness for dynamic registrations.

`orchestrator/http.go` uses a reverse proxy with immediate flushing, combines target
and request query strings, forwards the application path, and adds Livepeer session
control headers. Session cancellation cancels the proxied request. Existing tests
cover proxy paths/query behavior, SSE/WebSocket transport and lifecycle cases.

This is a suitable foundation. The node does not need to understand OpenAI schemas,
Anthropic blocks or model features. It should retain generic transport, registration,
health, capacity and payment responsibilities.

Registration is not proof of an honest capability claim. The observed proxy rewrite
also does not by itself remove gateway customer credentials: the gateway must keep
customer authentication separate from backend/Livepeer credentials.

The examples document a legacy 1 KiB metadata limit. The inspected node's heartbeat
validator has no separate metadata-length check; its control body and discovery body
limits are 1 MiB. Do not assume that every intermediary permits larger metadata.
Start with a compact document fitting the legacy limit, measure serialized UTF-8 bytes,
and test round trips through the actual SDK, node and discovery versions. A richer
contract requires an explicit size/version agreement, not an assumption.

## Runner deployment shape

Use an unmodified pinned vLLM, Ollama or SGLang server behind a statically registered
Livepeer route. An optional small deployment agent can handle runtime configuration,
readiness, contract generation, conformance results and dynamic registration. It should
not translate prompts or reimplement the provider protocols.

Generate the server settings and registration metadata from one deployment manifest.
Avoid hand-editing a model name in Compose and independently in runner registration.
For example, the served-model name, selected image, tool/reasoning parser configuration,
context limit and advertised endpoint profile must describe the same deployment.

Publish readiness only after the required model is loaded and the advertised endpoints
are usable. For lazy-loading runtimes, declare and test a cold-start policy; a process
that can answer a health request is not necessarily ready within the gateway deadline.

An upgrade or relevant configuration change creates a new deployment revision and
invalidates previous conformance evidence. Drain existing work before removing the old
registration. Do not swap models behind an unchanged revision/identity.

## Metadata: a small versioned contract

Keep model identity separate from the app name. Retain top-level `model_name` for
compatibility with the gateway's existing selection. Initially use one canonical model
per advertised route. Multi-model servers need a versioned per-model contract; separate
registrations must not each promise the full capacity of a shared server. Either enforce
a shared capacity pool or partition concurrency conservatively.

Proposed metadata fields:

| Field | Meaning | Owner/validation |
| --- | --- | --- |
| `inference_contract` | Schema major version | Gateway understands the major; unknown major is ineligible |
| `model_name` | Exact public/upstream model identity | Must match request and served model; never inferred from app |
| `runtime.name` | `vllm`, `ollama` or `sglang` | Supported-family policy |
| `runtime.version` | Actual deployed runtime version | Deployment agent/operator, verified against image |
| `deployment_revision` | Immutable image/model/config identity or digest | Changes when any compatibility-relevant input changes |
| `endpoints` | Known operation IDs and contract/usage profiles | Small gateway-owned profile registry, not arbitrary executable mappings |
| `limits` | Context and declared generation limits, where known | Report actual configuration; absence is unknown |
| Optional coarse features | Tools, strict tools, structured text, input modalities | For model cards and explicit feature-class selection; evidence attached separately |

Illustrative shape, not a shipped schema or a real deployment certificate:

```json
{
  "inference_contract": 1,
  "model_name": "example-model",
  "runtime": {"name": "vllm", "version": "<pinned-version>"},
  "deployment_revision": "<image-model-config-digest>",
  "endpoints": {
    "chat_completions": {"profile": "openai.chat.v1", "usage": "chat.final.v1", "stream": true},
    "responses": {"profile": "openai.responses.stateless.v1", "usage": "responses.final.v1", "stream": true},
    "messages": {"profile": "anthropic.messages.v1", "usage": "messages.events.v1", "stream": true}
  },
  "limits": {"context_tokens": 32768}
}
```

Each endpoint must actually be tested before inclusion. The example does not assert
that any server image supports all these profiles. Profile identifiers above are proposed
names; their semantics and tests must be defined before implementation.

Do not duplicate live price, capacity, mode or observation time in metadata: discovery
already carries them. Do not embed full JSON schemas, secrets, customer data, custom
code, or arbitrary remote URLs. Prices for retail remain gateway policy; wholesale
quotes remain the payment protocol's responsibility.

Use an operator/gateway-owned evidence registry keyed by deployment revision, model,
endpoint and profile. Record test-suite revision, result, verification time and expiry.
A self-asserted `verified:true` in runner metadata is not independent evidence.

Unknown additive metadata fields may be ignored; unknown major versions and required
profiles must not become eligible by accident. Missing or conflicting identity means
no dispatch. Distinguish malformed metadata, unsupported contract, unavailable capacity
and a known-but-unavailable model in internal diagnostics.

## Keeping the gateway lean

The gateway needs a bounded routing envelope, not a complete inference DTO:

- HTTP method and approved endpoint.
- Model identity and stream mode for generation.
- Authentication, tenant/key authorization, pricing eligibility and request limits.
- Explicit route/profile selection, where needed.
- A small set of state/lifecycle policy fields if the operation requires them.

Parse the envelope without reserializing the original body. Reject ambiguous routing
inputs, such as duplicate top-level model keys. Preserve unknown generation fields and
nested message/tool/schema structures. Do not traverse every tool schema on native paths.
The backend returns its own unsupported-field/schema error.

Select by `(model, endpoint, compatible profile)` and route policy before ranking by
capacity/health/cost. Apply the same eligibility rules on any allowed pre-payment retry.
Use immutable deployment identity to prevent fresh requests from being admitted against
stale capability evidence. Capacity snapshots remain advisory: handle a busy refusal
before payment without relaxing eligibility.

Metadata alone cannot tell the gateway what an arbitrary request needs. There are two
honest choices: route within a verified baseline profile and let the backend reject
extensions, or inspect a small explicit feature selector to select a stronger profile.
Do not infer every nested schema keyword and recreate the current compatibility parser.
If runners sharing a model/endpoint have different contracts, partition their route
classes so ordinary load balancing does not randomly change supported behavior.

New generation parameters should need backend updates, not gateway releases. A new
endpoint or a new usage/event format may legitimately require a small gateway adapter:
transport/accounting semantics cannot safely be guessed from arbitrary metadata.

## Native HTTP and SSE contract

Forward original request and response payloads, preserving the selected upstream status,
content type and permitted end-to-end headers. Preserve Anthropic version/beta headers
and relevant OpenAI protocol headers where the selected profile permits them. Rebuild
transport framing as necessary; HTTP hop-by-hop headers are not end-to-end application data.

Strip customer gateway credentials and forged internal/session headers. Supply only the
appropriate backend/Livepeer credentials. Do not forward arbitrary cookies, host overrides
or caller-controlled routing URLs. Maintain destination/TLS policy and bounded input.
Do not blindly relay backend cookies, internal credentials or unsafe redirects to clients.

Expose an explicit operation allowlist, initially the three generation POST endpoints
and gateway-owned model discovery. Wildcard proxying must not expose runtime administration,
model loading/deletion, debug endpoints or internal file resources. Model discovery should
aggregate eligible approved supply without paying for a generation session per list request.

Native backend errors retain their backend semantics after credential/internal-header
filtering. Gateway-originated auth/routing/payment/timeout failures get protocol-shaped
errors. After headers have started, transport failure must not fabricate a successful
terminal event. Never retry an uncertain paid generation on a different runner.

Bound request sizes, parser buffers, output volume and time. Backpressure and cancellation
must propagate through gateway, Builder Core, node and backend. Do not buffer the whole
SSE response merely to observe usage. Unknown non-accounting events can pass through intact;
unrecognized/missing authoritative usage remains explicitly unresolved.

## Accounting without rewriting generation

Usage observation is read-only and protocol-specific, separate from content translation.
A bounded observer sees the same bytes forwarded to the client and extracts usage,
terminal outcome and identifiers. Unknown events are not dropped from the client stream.

| Endpoint | Required accounting contract |
| --- | --- |
| Chat Completions | Unary usage and final streaming usage; recognize cached input correctly |
| Responses | Usage/outcome in terminal response events; handle incomplete/failed responses explicitly |
| Messages | Combine initial and final usage events according to the protocol; avoid double counting |

Chat streaming often requires `stream_options.include_usage`. This is a real tension
with byte-for-byte forwarding. Choose explicitly: configure a runner/profile that reports
usage without request changes, obtain correlated authoritative usage out of band, or offer
a documented route mode that adds only usage reporting. If none applies, do not promise
metered streaming for that route. Metadata cannot make missing usage appear.

Keep the retail price version, request/job/attempt correlation and idempotent charge
recording. Preserve measured usage on disconnect when available. Missing usage stays
pending under the current gateway policy; do not estimate it silently. Wholesale cost
and token usage remain separate evidence. Never count provider-reported tokens as an
independent measurement or let a runner's metadata set retail prices.

In native mode, strict schema enforcement belongs to the backend. Gateway validation
of usage and transport framing is not a claim that it validated tool arguments. Existing
validated mode retains its stronger output guarantee. Document the mode and migration
boundary clearly for clients relying on that guarantee.

## State and operations that cannot be freely load-balanced

Start with stateless generation. Stored Responses, `previous_response_id`, background
jobs, files, conversations and retrieval/cancel operations may bind to a particular
backend. Supporting them requires ownership/tenant authorization, durable origin mapping,
retention, deletion, recovery and billing after the initial HTTP response ends.

A Livepeer single-shot session covers the HTTP call, not necessarily a background job
that continues after an immediate response. Do not enable background operations merely
because a server has the endpoint. Keep them disabled/rejected under an explicit profile
until their lifecycle and payment contract exists. If enforcing stateless behavior requires
examining a few top-level fields, that is justified envelope policy, not prompt processing.

Some runtimes may store responses by default. Require verified server configuration or an
explicit, documented request transformation to meet the chosen stateless/retention policy;
absence of `previous_response_id` alone does not prove that no state is retained.

## Automated onboarding rather than per-feature gateway code

The target is low operator effort through automation, not zero verification:

1. Generate pinned runtime settings and compact registration from one manifest.
2. Check model readiness, served identity and protocol headers/endpoints.
3. Run conformance fixtures for the advertised profiles, including tools/schema cases
   when claimed, JSON/SSE, usage, errors and cancellation.
4. Store sanitized evidence tied to the immutable deployment revision.
5. Admit the route only for the approved profiles, with a freshness policy.
6. Withdraw or quarantine on drift/repeated protocol failures; preserve in-flight work.

Do not probe paid routes on each customer request. Use local/offline checks first and
explicitly authorized live acceptance with a budget. This review did not run live probes.

The same fake protocol fixtures should exercise the raw forwarding path and byte-preserving
observers. Model quality/tool execution reliability is a separate acceptance concern from
protocol transport. Never publish credentials, prompts or tool payloads in probe logs.

## Changes by owner

| Owner | Required work |
| --- | --- |
| Runner examples | Pin runtime images; add vLLM/Ollama/SGLang deployment variants; generate model/endpoint metadata; readiness and conformance instructions; correct app-only selection guidance |
| livepeer-node | Keep inference-agnostic proxy; document metadata round-trip/size guarantees; verify static/dynamic inventory propagation, headers, SSE, cancellation and shared-capacity implications |
| Builder Core integration | Prove raw HTTP body/status/header/SSE support and endpoint-aware selection; expose only supported embedding interfaces; retain payment/usage correlation |
| inference-gateway | Add native proxy port distinct from `Generate`; routing envelope, metadata admission, byte-preserving proxy, usage observers, operation allowlist, fake/SDK conformance tests |
| Operator/deployment tooling | Single source of deployment configuration, evidence registry, expiry/draining, secrets/TLS and resource limits |

No model-specific JSON-schema implementation should move into livepeer-node. Node code
changes are only needed where transport/registration acceptance exposes a missing generic
capability. The reviewed node already has the basic opaque metadata and HTTP/SSE plumbing.

## Acceptance before native mode ships

- All three backend families have at least one pinned configuration with declared tested
  endpoint coverage; do not require every backend to support every optional feature.
- Unknown request fields, local schema references and native event extensions pass through
  unchanged; gateway does not weaken or reconstruct generation content.
- Model/endpoint/profile mismatches and stale identities are excluded before payment.
- Headers, query, status, content type and fragmented SSE survive the complete paid path.
- Customer secrets never reach the runner; runtime management routes are inaccessible.
- JSON/SSE usage, cached usage, failure and cancellation preserve one charge and job correlation.
- Missing usage is unresolved; partial/invalid transport never appears as successful completion.
- Shared-capacity registrations cannot over-admit one physical backend.
- Storage/background behavior matches the admitted profile; no accidental stateful promises.
- Existing translated/validated clients retain their documented behavior until explicitly migrated.

See the [implementation plan](../exec-plans/active/native-inference-forwarding.md).
