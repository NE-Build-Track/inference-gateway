# Live-runner scaffold

Status: completed offline slice, 2026-10-27. Native forwarding remains in the
[active plan](../active/native-inference-forwarding.md).

## Acceptance and implementation

One validated manifest generates runtime arguments, compact versioned metadata,
readiness settings and offchain node configuration for vLLM, Ollama and SGLang.
Model identity comes from `model_name`, independently of app identity. Images and
model revisions are immutable; configuration changes invalidate deployment identity.

The shared contract rejects unknown versions, arbitrary endpoint paths, invalid pins
and oversized metadata. Fixed profiles describe Chat Completions, stateless Responses
and Anthropic Messages, including usage expectations. These are candidate declarations,
not runtime certification. Readiness checks model visibility and health; Ollama also
requires the expected loaded digest. Runtime ports remain private. Registrations stay
idle because existing gateway selection does not enforce candidate admission.

## Verification

- `make check`: 419 tests passed, plus lint, formatting, typing and documentation checks.
- Compose configuration validation passed for all three families with synthetic model
  revisions used only for configuration validation.
- `uv build`: wheel and source distribution built successfully.

No containers were started, models downloaded or live/paid inference requested.
Image digests are registry-resolved candidates. Native transport/selection, accounting,
admission evidence, streaming/cancellation conformance and GPU/live acceptance remain.
