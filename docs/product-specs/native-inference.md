# Native inference routes

Status: opt-in implementation with offline, bounded GPU and paid accounting acceptance.
Runtime families are **vLLM, Ollama and SGLang**; the first release target is the tested
vLLM configuration, with the other families experimental. Bundles remain candidates pending
release review and deployment approval. No generic feature-parity claim is made.

## Routing and forwarding

Set a model route's `mode` to `native` to use original HTTP bodies instead of generation
translation. The default is `validated`, retaining existing strict-schema enforcement.
A model cannot mix modes across its routes. Native strict tools, schema references,
reasoning, sampling and generation-specific extensions belong to the backend; the gateway
does not inspect or enforce their schemas. This is a different guarantee from validated mode.

Native routes support the fixed paths `/v1/chat/completions`, `/v1/responses` and
`/v1/messages` only when the runner's metadata and independent operator approval name the
requested endpoint profile. App names are not model identities. Selection checks the
configured app, `model_name`, origin, runner ID, deployment revision, positive capacity,
discovery freshness and approval expiry before dispatch. The same policy is installed in
Builder Core selection, so ranking/failover cannot widen eligibility. Model catalogs include
eligible native models and retain retail-price filtering in registry mode.

The gateway authenticates and authorizes before dispatch, snapshots the retail price, then
forwards original JSON bytes through Builder Core. Unknown generation fields and protocol
extension events survive unchanged. Only routing/accounting/state fields are inspected:

- `model` must identify a configured native route; `stream` must be a boolean.
- Chat streaming requires explicit `stream_options.include_usage: true`; the gateway never
  adds it silently.
- Responses requires explicit `store: false`. Background requests, previous-response IDs
  and conversation state are rejected. Backend stateless configuration still requires acceptance.
- Duplicate JSON keys, non-finite JSON numbers, encoded request bodies and query parameters
  are rejected. No query extension profile is currently admitted.

Request headers use an allowlist: content type, accept, Anthropic version and beta fields.
Customer keys, cookies, hop-by-hop and Livepeer payment headers do not pass to the runner.
Native Anthropic version values are backend-owned; validated mode retains its existing
version check. Response status and body are preserved, including backend HTTP errors.
Only approved public response headers pass; gateway request IDs remain authoritative.
Compressed backend responses fail closed because usage observers currently require identity
encoding. The SDK requests identity encoding. Unary response bodies are bounded to 8 MiB;
SSE observations have bounded frame storage. No wildcard management API is exposed.

## Accounting and lifecycle

The native path binds the gateway request to the Builder Core job before consuming bytes.
Optional null token-detail objects are treated as absent, with zero reported cached tokens.
Messages streams use final cumulative input/cache counts from `message_delta` when
present, retaining `message_start` fields only when not updated. Malformed detail objects
or token counts still fail usage validation. Runner configuration
enables cache reporting where supported; the gateway does not infer unreported savings.
Read-only observers extract unary or streamed usage for all three protocols, including cached
input. Measured retail usage is recorded once, and completed transport usage is reported to
Builder Core. Wholesale reconciliation remains separate and does not rerun inference.

Missing or malformed usage does not become a successful measured generation or an invented
charge. Valid Responses `failed`/`incomplete` payloads remain visible unchanged and record a
failed gateway outcome, retaining any measured usage. Builder Core's HTTP-200 transport
outcome is separate from the provider's generation outcome. Non-2xx backend responses retain
their status/body and fail the underlying native job; they do not trigger a gateway retry.

Deadlines include opening, consuming and delivering the response. Disconnects notify the
backend before owned iterators close. Failures before response headers return a sanitized
protocol error. Failures after headers abort delivery without appending invented SSE events.

## Configure an accepted deployment

Example route configuration:

```json
[{"name":"served-model","app":"inference/vllm","mode":"native"}]
```

Supply it in `GATEWAY_MODEL_ROUTES` with `GATEWAY_BACKEND=livepeer`. Existing registry,
price and LPB payment configuration still applies. Supply `GATEWAY_NATIVE_APPROVALS` as a
JSON array of operator-owned evidence records with these fields:

| Field | Meaning |
| --- | --- |
| `orchestrator` | Exact HTTP(S) origin, also subject to the configured origin allowlist |
| `runner_id`, `app`, `model` | Accepted supply identity; `model` equals metadata.model_name |
| `deployment_revision` | Exact SHA-256 revision generated by the runner bundle |
| `endpoint` | `chat_completions`, `responses` or `messages` |
| `expires_at` | Finite future UTC Unix timestamp |

An empty approval array admits no native traffic. Approvals are loaded at startup; revocation
requires replacing the configuration and restarting the gateway. Expiry is checked during
selection. No automatic certification, promotion or approval-writing command is shipped.
Do not approve a candidate solely from metadata or image family. Generated runners remain
idle until the operator has completed the required deployment acceptance.

## Dependency and release status

The gateway pins Builder Core `0d0b532` from
[NE-Build-Track PR #55](https://github.com/NE-Build-Track/livepeer-builder-core/pull/55),
which pins SDK `cd6ed5e` from
[NE-Build-Track PR #6](https://github.com/NE-Build-Track/livepeer-python-gateway/pull/6).
These are immutable review revisions, not merged release claims. Publication is restricted
to NE-Build-Track forks. No upstream Livepeer PR or push is part of this release chain.

See the [runner bundle](../../live-runner/README.md) and
[active plan](../exec-plans/active/native-inference-forwarding.md). Remaining acceptance
covers the exact runtime deployments, node metadata propagation, real paid settlement and
shared-capacity behavior. The development stack stays stopped.

## Input modalities

See [native model inputs](native-inputs.md) for image payload examples, request limits and
the distinction between text, image, audio, video and document inputs. Native forwarding
does not decode media or certify a model capability merely because it accepts JSON.

## Runner identity without a node patch

New bundles place `runner_id` in metadata using the same value as the node registration ID.
When discovery omits its top-level `runner_id`, the gateway's discovery adapter fills it
from validated metadata before Core caches or selects runners. If both IDs are present,
they must match; conflicts are excluded from discovery and rejected by native selection.
Missing IDs are never guessed from app names, URL paths or proxy hostnames. Transport
continues to use the original advertised URL. Legacy metadata without a runner ID remains
compatible when discovery supplies a top-level ID.

This is identification, not authorization. The independent approval still binds the
orchestrator origin, runner ID, app, model, deployment revision, endpoint and expiry. Capacity
and freshness checks still apply. The existing 1,024-byte metadata bound includes the new
field; the renderer rejects oversized declarations rather than truncating them.

Node PR #1 is not required. The node lock now pins unmodified source commit `dfcf033`.
Deploy the updated gateway before advertising the new metadata: older gateway contracts
reject the new field. Regenerate runner configuration, then issue approvals for its new
deployment revision; old revisions are not automatically promoted. Use matching
`native-metadata-20261027` gateway/node candidates, not the older native-vllm gateway image.

Native startup requires Core's raw-body, content-type, protocol-header and HTTP-error
request fields plus its response-header stream facade. An incompatible dependency
installation fails before engine creation with a diagnostic to restore the locked
versions. Latest M3 cannot currently replace those pins; see the
[28 October review](../references/repository-compatibility-2026-10-28.md).
