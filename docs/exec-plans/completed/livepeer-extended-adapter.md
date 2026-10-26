# Livepeer adapter for observed text capabilities

## Acceptance

Extend the paid adapter for structured output, reasoning, stop sequences and parallel tools.
Capabilities are explicit per model route and default to disabled. Existing text/tool requests
and failure/cancellation/accounting ownership must continue to work. Unsupported features,
controls and history fail before paid dispatch, including on otherwise enabled routes.

- Forward JSON object/schema output formats; independently validate complete final text and
  fail invalid structured output without a success terminator. Preserve truncation outcomes.
- Decode separate reasoning incrementally before final text/tools, preserve history, and never
  fabricate provider signatures. Controls the runner cannot honor remain unsupported.
- Forward stop sequences; withhold matching text across chunk boundaries. Report a stop sequence
  only when observed or explicitly reported by the runner; never guess from natural termination.
- Forward explicit parallel-tool selection and emit independently indexed interleaved calls when
  enabled. Validate all identities/arguments and bound output; preserve the old normalized path.
- Verify both official SDKs in JSON/SSE modes, malformed output, usage, cancellation and deadlines.
- Run make check, then dated live acceptance through the updated adapter if the configured paid
  environment is available. Record operational failures separately from missing capability.

## Work

1. Route capability contract and request translation.
2. Decoder and stop filtering; SDK and failure contracts.
3. Reproducible live acceptance runner and safe deployment/isolated validation.
4. Update compatibility, gap register and verification evidence; complete only verified work.

## Decisions and evidence

Completed 2026-10-26. All four scoped capabilities are implemented behind per-route opt-in.
Reasoning uses the explicit on/off switch; effort/budgets and nonempty signatures remain
unsupported. Stop filtering preserves measured usage and reports only observed/runner-supplied
sequences. Parallel calls stream independently; existing callers retain sequential normalization.
Structured final text is independently validated before success.

Verification: make check passes with 252 tests, strict typing, lint/formatting and documentation
checks. Real-engine scripted transports cover cancellation and deadlines on every new path.
The built image matched all 32 source-file hashes in the working tree.

Live matrix: 15/16 passed. The remaining Anthropic parallel-stream case passed in a separate
targeted follow-up; the initial assertion cause was not retained, so both outcomes remain in
the evidence. Two production-mode smoke requests passed with matching ledger usage and one charge
each. The temporary key was revoked and its user disabled. The local port-8000 deployment now
enables the four capabilities; the isolated validation server was removed. Existing credentials
and prices were preserved. TLS failures on two US-central origins were handled by selecting
three already trusted origins without weakening verification.

The [gap register](../livepeer-capability-gaps.md) links 34 isolated engine operations, two
production smoke requests, source/image hashes and remaining capability/operational work.
Acceptance does not certify effort/budget controls, provider signatures, every JSON Schema,
semantic sampling behavior or deterministic model tool selection.
