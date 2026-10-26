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
