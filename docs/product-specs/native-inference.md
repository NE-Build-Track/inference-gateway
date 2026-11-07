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

Native routes support the fixed paths `/v1/chat/completions`, `/v1/responses`, `/v1/messages`
and [`/v1/systemone`](system-one.md) only for runners that declare the endpoint's profile in their
[runner contract v1](runner-contract.md) envelope and hold an operator approval for it. Selection
checks the route's capability, the envelope's model name and profile, origin, runner ID, deployment
revision, positive capacity, discovery freshness and approval expiry before dispatch. The same
policy is installed in Builder Core selection, so ranking/failover cannot widen eligibility. Each SDK's
[model list](models-and-responses.md#model-lists) includes only the native models eligible for its
endpoints and retains retail-price filtering in registry mode. Calling a model on an endpoint it
has no approval for returns 400 before payment; a temporary shortage stays 503. A body that breaks
the endpoint's request schema in the registry returns 400 before payment.

The gateway authenticates and authorizes before dispatch, snapshots the retail price, then
forwards original JSON bytes through Builder Core. Unknown generation fields and protocol
extension events survive unchanged. Only routing/accounting/state fields are inspected:

- `model` must identify a configured native route; `stream` must be a boolean.
- Chat streaming requires explicit `stream_options.include_usage: true`; the gateway never
  adds it silently.
- Responses requires explicit `store: false`. Background requests, previous-response IDs
  and conversation state are rejected. Backend stateless configuration still requires acceptance.
- System One rejects `stream: true`, `images`, `videos`, `assets` and `media_kwargs`, because
  SGLang would mishandle or ignore them. See its [contract](system-one.md).
- Duplicate JSON keys, non-finite JSON numbers, encoded request bodies and query parameters
  are rejected. No query extension profile is currently admitted.

Request headers use an allowlist: content type, accept, Anthropic version and beta fields.
Customer keys, cookies, hop-by-hop and Livepeer payment headers do not pass to the runner.
Native Anthropic version values are backend-owned; validated mode retains its existing
version check. Response status and body are preserved, including backend HTTP errors.
Only approved public response headers pass; gateway request IDs remain authoritative.
Compressed backend responses fail closed because usage observers currently require identity
encoding. Builder Core requests identity encoding. Unary response bodies are bounded to 8 MiB;
SSE observations have bounded frame storage. No wildcard management API is exposed.

## Accounting and lifecycle

The native path binds the gateway request to the Builder Core job before consuming bytes.
Optional null token-detail objects are treated as absent, with zero reported cached tokens.
Messages streams use final cumulative input/cache counts from `message_delta` when
present, retaining `message_start` fields only when not updated. Malformed detail objects
or token counts still fail usage validation. Runner configuration
enables cache reporting where supported; the gateway does not infer unreported savings.
Read-only observers extract unary or streamed usage for the three generation protocols, including
cached input, and unary usage for System One, where zero output tokens is valid. Measured retail usage is recorded once, and completed transport usage is reported to
Builder Core. Wholesale reconciliation remains separate and does not rerun inference.

Missing or malformed usage does not become a successful measured generation or an invented
charge. Valid Responses `failed`/`incomplete` payloads remain visible unchanged and record a
failed gateway outcome, retaining any measured usage. Builder Core's HTTP-200 transport
outcome is separate from the provider's generation outcome. Non-2xx backend responses retain
their status/body and fail the underlying native job; they do not trigger a gateway retry.
Builder Core still fails over a 503, 404 or 409 to another runner first, as for any job (its
D52, at most `LPB_MAX_PAID_ATTEMPTS` paid attempts); only the answer it stops at is returned.
Forwarded responses with status 400 or above carry `x-should-retry: false`, so default OpenAI
and Anthropic SDK retries do not repeat a paid request. The TypeSafe SDK ignores that header, so its
clients must [disable retries](system-one.md#configure-the-typesafe-sdk).

Deadlines include opening, consuming and delivering the response. Disconnects notify the
backend before owned iterators close. Failures before response headers return a sanitized
protocol error. Failures after headers abort delivery without appending invented SSE events.

## Configure an accepted deployment

Example route configuration:

```json
[{"name":"served-model","mode":"native"}]
```

Supply it in `GATEWAY_MODEL_ROUTES` with `GATEWAY_BACKEND=livepeer`. Existing registry,
price and LPB payment configuration still applies. Native traffic also needs approvals in the file
named by `GATEWAY_APPROVALS_FILE`, each carrying the runner's passing conformance report; the
[runner contract](runner-contract.md#admission-and-approvals) lists their fields. The gateway
re-reads the file when it changes, so approving or revoking needs no restart. With no file, the
gateway admits no native traffic.

No automatic certification, promotion or approval-writing command is shipped. Do not approve a
candidate solely from metadata or image family: approve the deployment revision its conformance
report covers.

## Dependency and release status

The gateway pins Builder Core `releases/m3` at `1514212`, the M3 merge, which includes `2134e29`, the merge of
[NE-Build-Track PR #55](https://github.com/NE-Build-Track/livepeer-builder-core/pull/55).
Core pins the SDK to upstream `livepeer/livepeer-python-gateway` `2017a80`; the earlier SDK fork
pin (`cd6ed5e`, NE PR #6) is gone. Deployment images build Node and Clearinghouse Batteries from
`livepeer/` `main`. Image publication is restricted to NE-Build-Track infrastructure; no upstream
Livepeer PR or push is part of this release chain.

See the [runner bundle](../../live-runner/README.md) and
[active plan](../exec-plans/active/native-inference-forwarding.md). Remaining acceptance
covers the exact runtime deployments, node metadata propagation, real paid settlement and
shared-capacity behavior. The development stack stays stopped.

## Input modalities

See [native model inputs](native-inputs.md) for image payload examples, request limits and
the distinction between text, image, audio, video and document inputs. Native forwarding
does not decode media or certify a model capability merely because it accepts JSON.

## Runner identity without a node patch

Bundles place `runner_id` in the envelope with the same value as the node registration ID, which the
standard allows when the node doesn't report one. When discovery omits its top-level `runner_id`,
the gateway's discovery adapter fills it from the envelope before Core caches or selects runners. If
both IDs are present, they must match; conflicts are excluded from discovery and from selection.
Missing IDs are never guessed from App IDs, URL paths or proxy hostnames, and a runner without one
can't match an approval. Transport continues to use the original advertised URL.

This is identification, not authorization. The approval still binds the orchestrator origin, runner
ID, model, deployment revision, profile and expiry. Capacity and freshness checks still apply. The
1,024-byte metadata limit includes the field; the renderer rejects oversized envelopes rather than
truncating them.

Native startup requires Core's raw-body, content-type, protocol-header and HTTP-error
request fields plus its response-header stream facade. An incompatible dependency
installation fails before engine creation with a diagnostic to restore the locked
versions. The [28 October review](../references/repository-compatibility-2026-10-28.md) found
M3 `efb9c03` lacked them; the pinned Core `1514212` has them (see the
[29 October repin evidence](../../live-runner/tests/m3-upstream-2026-10-29.md)).
