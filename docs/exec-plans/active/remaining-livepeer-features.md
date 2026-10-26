# Remaining Livepeer generation features

## Acceptance

Complete gateway/adapter behavior for remaining sampling controls, reasoning effort/budgets,
citations, hosted search and media. Do not substitute synthetic output for live capability.
Each route advertises only implemented, verified behavior. Keep unsupported requests distinct
from unsupported responses and operational failures. Preserve paid usage, cleanup and deadlines.

- Sampling and reasoning controls: explicit validated forwarding, route-specific effort support,
  budget mapping and tests that prove delivery; live probes must not mistake HTTP acceptance for
  semantic evidence.
- Citations: preserve user source/document provenance and validate references/quoted spans before
  success. If using a structured generation workflow, document buffering and actual runner usage.
- Hosted search: one bounded server execution with real search evidence, explicit collaborator and
  configuration; never turn a model function call into a claim of executed search.
- Media: preserve native input formats and actual encoded output, assemble bounded streaming output,
  validate format, and probe available model/runner routes. Never enable unsupported media on a text
  route or return fake bytes from a live request.
- Verify SDK JSON/SSE contracts, failures, cancellation, history, route isolation and accounting.
  Run make check and dated live acceptance. Deploy only the verified subset and record any external
  prerequisite that cannot be supplied from this repository/environment.

## Work and evidence

In progress: inspect available runners and integration resources before choosing external providers.
Existing source changes and production credentials/prices are preserved.


## Implemented and verified locally

- Explicit sampling forwarding; effort allowlist and vLLM budget mapping; explicit media-kind
  allowlist with native image/WAV/MP3 translation and content-order preservation.
- Unknown/incoherent route options and unsupported history/formats fail before paid dispatch.
- SDK JSON/SSE coverage for both protocols verifies actual engine payloads and successful output.
- Probe coverage now includes low/high effort, thinking budget, valid 128px image and one-second
  WAV fixtures. Image assertions test the controlled color; budget/effort acceptance stays
  inconclusive without effective-parameter/token evidence. Reports persist after every case.
- A new live pass completed baseline plus thirteen features; direct reports and sanitized engine
  exports are linked in the gap register. Production configuration was not expanded.

## External work still open

The original acceptance above is not all achieved. Discovery provides one chat app and separate
speech/transcription/image apps, not an established combined chat media/search/citation contract.
No hosted search executor is configured. The current runner rejects media inputs/high effort and
omits citation/media output fields. Sampling, low effort and budget effects remain unverified.
The [gap register](../livepeer-capability-gaps.md#remaining-controls-pass-2026-10-26) records concrete
runner changes and acceptance for each. Keep this plan active until those integrations are verified;
do not describe forwarding or advertised non-chat apps as completed live capabilities.

## Verification

`make check` passed: 279 tests, strict typing, Ruff and documentation checks. `git diff --check`
passed. Live raw probes recorded 14 paid single-attempt operations: 11 completed streams and
three HTTP 400 runner rejections. Complete-stream success alone did not certify the requested
features. No production image/configuration changes were made in this pass; newly implemented
forwarding is available in the working tree and remains guarded pending runner verification.
