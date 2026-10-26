# Extended generation contracts and backend capability checks

## Problem and acceptance

Implement the seven deferred conversational feature groups in the gateway and deterministic
fake before evaluating Livepeer. Preserve existing text/tool behavior and unrelated working-tree
changes. Acceptance requires both JSON and SSE contracts, SDK consumption, explicit validation,
resource cleanup, and no silent loss of requested features.

- Reasoning: separate reasoning and final text, with historical reasoning round trips.
- Sampling: validated top-p/top-k, penalties, seed and stop controls; fake remains deterministic.
- Structured output: JSON object and a documented JSON Schema subset, including independent
  validation of completed output; truncation must not claim schema success.
- Citations: source references on text, preserved in JSON and SSE.
- Hosted tools: distinct server calls/results; fake simulates execution without external I/O.
- Parallel tools: overlapping blocks keyed by index; assemble each call independently.
- Images/audio: validated input references/data and deterministic output fixtures. Do not fetch URLs.

Native protocol fields are preferred. Explicit gateway extensions cover features without a native
representation; they must not be described as provider API parity. Unsupported combinations fail.

## Steps

1. Extend domain values, input translation, event validation and wire encoders.
2. Extend the fake and test public SDK contracts plus failures and cleanup.
3. Gate unverified Livepeer capabilities before paid dispatch; distinguish unsupported requests
   from unsupported responses and operational failures.
4. Prepare route-specific capability probes and a gap register. Run live probes only when the
   configured environment is available, preserving unknown/inconclusive status otherwise.
5. Update compatibility, backend and quality documentation; run make check and package build
   if package metadata changes. Move this plan to completed only after verification.

## Verification and decisions

Completed 2026-10-26. All seven feature groups have explicit gateway/fake contracts, with native
fields where applicable and documented extensions elsewhere. Structured output has a validated
schema subset; hosted tools currently cover one synthetic web search. Unsupported combinations
are rejected. The service validates independent overlapping client calls, citation spans, media
encoding, structured output, closure and usage while bounding output.

Verification: make check (197 tests, strict typing, lint/formatting and documentation links).
Sixteen paid probe operations established the dated observations in the
[Livepeer gap register](../livepeer-capability-gaps.md). Runner support is not automatically
promoted to production adapter support. Extended requests/history remain guarded before paid
dispatch; unsupported responses have a distinct public error code. The container was replaced
externally before probe database export; captured sanitized stdout preserves job IDs and results.
The report transcriptions document this limitation. No production deployment was performed.

The fake proves protocol behavior, not model quality or backend support. Livepeer
capabilities require dated probe evidence and separate adapter acceptance before enablement. Existing interleaved runner
calls are already normalized into sequential output; this is separate from native interleaving.
