# Initial design decisions

Recorded 2026-10-25. User requirements: two compatible interfaces, a pluggable fake
backend, text, streaming, tool calls, and room for additional endpoints.

## One generation context, explicit boundaries

The architecture applies Evans's Bounded Context and Anticorruption Layer ideas:
one local generation vocabulary, translated at external protocol boundaries. There
is no persistent identity or transactional invariant that warrants aggregates or
repositories. This keeps the model small; translating two protocols is the accepted
maintenance cost. Revisit the context boundaries if routing, account policy, or billing
acquires distinct rules and ownership.

## Pure dependency injection

The service receives a backend through its constructor. `create_app` is the composition
root and receives configuration values or loads them at startup. A `Protocol` expresses
the replaceable behavior; unit tests need no container. Strict typing and composition
exercises catch wiring errors. Reconsider a container if several entry points share a
large graph or request-scoped resources require substantial cleanup orchestration.

The scaffold is one application distribution with an import-isolated core. A separate
library distribution adds release overhead before there is an external consumer.
Split packaging when reuse requires independent versioning; preserve the public port.

## Request/reply with incremental translation

Hohpe and Woolf's Remote Procedure Invocation fits callers waiting for a generation.
Message Translator describes the protocol conversion, and Correlation Identifier
informs request tracing. These ideas do not require a broker. SSE preserves output
order within one request and passes backpressure through awaited sends.

A broker would add latency and operational state without improving this synchronous
path. Revisit messaging for durable jobs or asynchronous workflows, with explicit
retry, deduplication, and failure recovery requirements.

## Stateless operation and bounded generation time

Following the data-system guidance, no storage is introduced without a durability
requirement. Clients own conversation history; request IDs are diagnostic, not an
exactly-once guarantee. There are no automatic retries: a future paid upstream call
can have an unknown outcome after a timeout. The default deadline is an operational
starting value, not a measured latency SLO. Load, throughput, and retention requirements
have not been supplied.

Deadlines use a monotonic clock. Wall time is used only for the OpenAI response timestamp.
No output is replayed after failure. Add persistence when a defined feature requires it,
and specify its consistency and recovery guarantees then.

## Agent-oriented feedback

The harness approach is implemented through a short repository map, linked local
contracts, architecture tests, strict typing, repeatable setup, CI, request correlation,
and execution records. Errors in mechanical checks point to the relevant boundary.
This introduces maintenance work for docs and checks; it makes future changes reviewable
without reconstructing decisions from chat. A full observability stack and scheduled
cleanup agents remain deferred until their operating needs are defined.

## Extended generation contracts before backend enablement

The gateway and fake define seven feature groups before Livepeer adapter enablement. Domain
values distinguish reasoning, citations, media and hosted results from client function calls.
Only client tool blocks may overlap; keyed assembly preserves their identities and arguments.
Native protocol representations are used where available; explicit gateway extensions cover
missing equivalents. This avoids inventing provider parity or losing information in translation.

Structured final output uses a small, validated JSON Schema subset rather than accepting arbitrary
keywords the fake cannot honor. The fake demonstrates wire behavior using synthetic fixtures;
it cannot prove model behavior, grounding, hosted execution or sampling effects. Extend the subset
when a concrete client needs it and tests establish generation and validation behavior.

Livepeer checks extended requests/history against per-route capabilities before dispatch.
Runner probes record observed, unsupported-response, inconclusive and operational outcomes
separately. Probe success does not automatically enable production capabilities. See the
[extended contract](../product-specs/extended-generation.md) and
[capability gaps](../exec-plans/livepeer-capability-gaps.md).


## Livepeer capability opt-in and response evidence

A route declares `structured_output`, `reasoning`, `stop` or `parallel_tools`; the default is
empty. The adapter independently rejects unsupported controls and signed reasoning history even
on enabled routes. An on/off thinking switch must not silently stand in for a token budget or
effort level. Empty Anthropic signatures identify the absence of a provider signature; no signature
is fabricated. Revisit when the runner supplies an explicit compatible control/signature contract.

The stop filter withholds possible marker prefixes and strips matched markers while the adapter
continues reading bounded runner output for actual usage/termination. It reports a stop sequence
only from observed text or matching runner metadata. It never subtracts guessed token usage.
Parallel opt-in streams independently identified calls; the old sequential normalization remains
for existing callers. Model tool selection is separate from transport support for interleaving.

The initial live matrix and follow-up are both retained rather than replacing failed evidence with
a clean report. Temporary acceptance servers use separate durable engine databases, and sanitized
reports are written to the host after every case. The accepted image can be enabled locally after
SDK, lifecycle and live checks; deployment is not equivalent to broad provider parity.


## Model metadata and stateless Responses

Model identity comes only from runner JSON metadata `model_name`, matching the approved route name
and upstream model field. App identity remains the network application. The gateway embeds an
explicit model-filtering selection policy and chooses a compatible app before paid dispatch; it
does not modify Builder Core or introduce cross-app paid retries. Empty filtered selections raise
NoCapacity rather than returning an empty sequence to the pinned engine's attempt loop.

Responses is a stateless protocol adapter over generation, with an optional native Responses
runner transport. Storage, background jobs and previous-response references are rejected rather
than treated as ordinary generation. Native output is validated before successful termination and
uses the same usage recording/settlement path. Revisit with an explicit storage/lifecycle contract
if a client needs stored Responses, or with structured capability metadata if runners sharing a
model/app pair have different endpoint support.


## Native protocol forwarding as the preferred direction

Accepted 2026-10-27. Support vLLM, Ollama and SGLang as the LLM runtime families,
qualified by exact deployment and endpoint evidence. Prefer native forwarding over
normalizing every supported backend request through `Generate`. Keep auth, model/endpoint
selection, transport limits, payment and bounded usage observation in the gateway; let the
backend own generation schemas and extensions. Translation and gateway-enforced validation
remain explicit modes. Current runtime behavior is unchanged until the new mode is built.

Versioned metadata describes identity and protocol/usage profiles, not arbitrary executable
routing logic or self-certified trust. Keep livepeer-node inference-agnostic. The
[native inference design](native-inference-routing.md) supersedes the assumption that all
future inference must pass through the normalized generation contract, but does not remove
existing compatibility guarantees or authorize silent accounting/storage changes.

## System One and per-SDK model lists

Accepted 1 November 2026. Serve decision models by forwarding `POST /v1/systemone` natively, since
they generate no text and nothing can translate them to chat.

Accepted 2 November 2026. Admit `images` only when each entry carries the image itself, because SGLang
opens local paths and SGLang and vLLM fetch URLs. Accept both inline forms, a `data:` string and bare
base64, and leave the choice to the client: backends disagree about which they take, and Ollama takes
only the bare form. One rule then covers every backend, with no per-backend logic in the gateway. Check
that the bytes are an image, because llama.cpp passes audio sent through `images` to a model that can
hear it.

Keep refusing `audio`, `videos`, `files` and `assets`. SGLang, Ollama and llama.cpp answer as if
`audio` were absent, so a customer would pay for a decision that never heard it, and no check on the
request can tell. Revisit with a per-route setting once a deployment has proved its model uses them.

Do not limit System One to one runtime. The earlier rule that only SGLang serves it was wrong: all four
tested backends do.

Choose each `GET /v1/models` response from request headers, because all three SDKs call the same
path and parse different formats. A model appears only if it serves every endpoint the caller's SDK
can reach, which keeps a decision model away from chat clients and the reverse. For OpenAI this
means both chat and Responses, so a chat-only native model is callable but unlisted. Revisit if
chat-only runners become a supported target; the alternative is to list them and accept a 400 for
Responses clients. `X-TypeSafe-SDK` is an SDK detail rather than a protocol field. If TypeSafe
renames it, that SDK receives the OpenAI format and fails loudly, so it never shows a wrong model.

## Runner contract v1

Accepted 5 November 2026. Read runners only through the [live runner standard](https://github.com/NE-Build-Track/live-runner-standard),
runner contract v1, with no compatibility for this repository's earlier `inference_contract`
metadata. The standard is the network's shared contract between runners and gateways; a gateway
format of our own would make every runner speak it. Runners and the gateway move together.

Use the standard's Python library for envelopes, selection, request schemas and answer rules,
rather than reimplementing them, and pin both it and a registry release by commit. Keep it behind
`backends/`, like Builder Core, so the HTTP layer reaches schema checks through the native port.

Route by capability and envelope model name. App IDs no longer separate models, so two routes for
one model must differ by capability.

Require a passing conformance report with every approval, at the user's choice. Reports aren't
signed, so an approval records the operator's acceptance of the evidence, not a proof. Revisit when
the standard settles on continuous checks or attested evidence. Keep approvals in a file the gateway
re-reads, because the standard requires reloading without a restart and a report doesn't fit an
environment variable.

Validated routes admit `candidate` runners without approval, as before v1 they admitted any runner
on an allowlisted orchestrator; `private` runners need one. Native routes need one for every runner.

Follow each entry as written, including System One's closed request schema: `stream: false` and
unknown top-level fields now fail before payment. Revisit by proposing a new major of the entry, not
by loosening the gateway.

Keep `runner_id` in bundle envelopes while the pinned node doesn't report runner IDs in discovery.
Don't select runners whose envelope moves an entry's path or method, because Builder Core sends one
path per job and couldn't fail over between paths.
