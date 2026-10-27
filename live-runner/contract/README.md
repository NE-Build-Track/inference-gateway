# Runner contract version 1: candidate deployment

[deployment.schema.json](deployment.schema.json) describes operator input;
[metadata.schema.json](metadata.schema.json) describes the compact advertisement.
Both are generated from the shared [Python contract](../../src/inference_gateway/adapters/runner_contract.py).
Tests fail if the checked-in schemas or [profiles](profiles.json) drift from that code.

The Python validator additionally checks endpoint/profile coherence, metadata byte size,
model revision type, duplicate endpoints, and HTTP origin semantics. JSON Schema alone
is not the full admission validator. Version 1 currently permits only `candidate`
admission. Native gateway selection requires independent, expiring operator approval;
production certification remains external to this metadata.

Model identity is exact and separate from app identity. No app fallback or automatic
alias rewrite is permitted. One deployment advertises one model. All endpoints on that
registration share the orchestrator's capacity limit. Multiple registrations against
one physical engine require a shared capacity pool or explicit conservative partition;
this renderer does not create them.

## Fixed profile meanings

| Operation | Path | Accounting observer requirement |
| --- | --- | --- |
| `chat_completions` | `/v1/chat/completions` | Prompt/completion usage; cached input subset; streaming requires `stream_options.include_usage` |
| `responses` | `/v1/responses` | Input/output/cache usage from terminal response events; stateless profile only |
| `messages` | `/v1/messages` | Combine message-start/message-delta usage by protocol semantics, without double counting |

These profiles define test targets, not claims that a declared runtime passes. Native
requests retain backend validation semantics. Profiles do not promise every provider
extension or gateway-enforced strict schemas. Unknown operation IDs/profile mutations
are rejected; advertisements cannot supply arbitrary paths or executable usage extractors.

The native gateway requires explicit chat stream usage opt-in from the client. No renderer changes
customer requests. Missing usage cannot become a fabricated zero or successful charge.

The Responses profile requires verified retention/background policy. Some backend
versions store responses by default; an endpoint declaration alone is insufficient.
Neither storage policy certification nor stateful job affinity/payment is implemented
in this candidate scaffold. Do not admit stored/background calls on this basis.
