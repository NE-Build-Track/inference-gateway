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
