# Verification

[Offline tests](../../tests/test_runner_bundle.py) run in `make check` and need no Docker,
GPU, model download or live network. They exercise all runtime renderers, exact model
identity and revision propagation, generated TOML, metadata bounds, candidate admission,
configuration fingerprint changes, schema/profile synchronization and readiness failures.

`make check` also lints/formats `live-runner/tooling`. The shared contract is included
in strict source typing. Compose configuration can be checked without starting services.

Still required before admission: real pinned-runtime startup; loaded-model identity;
JSON/SSE per endpoint; tool/schema and parser behavior where claimed; usage and cached
usage; native payload/header/status preservation through gateway and node; cancellation,
timeouts, malformed output, paid failures and accounting; verified stateless retention.
Record sanitized results by deployment revision. No evidence file should claim these
checks passed merely because the renderer or readiness observer succeeded.

The [27 October remote GPU run](gpu-acceptance-2026-10-27.md) records real offchain
acceptance for the three pinned runtime families, fixes found, backend gaps, persistent
model storage and the remaining node-image release dependency. It does not certify paid
settlement, all models or every provider feature.

The [Qwen3.8 follow-up](qwen38-followup-2026-10-27.md) adds adversarial schemas, combined
output/tool choices, tool-result follow-ups, deadlines/recovery, published candidate
images and four paid calls. Chain finality remains separate from observed costs.

The [vLLM release follow-up](vllm-release-2026-10-27.md) records recovery, fresh paid
gateway acceptance, signer discovery identity, final candidate images and cleanup.


## Opt-in image acceptance

`probe_vision.py` submits synthetic red/blue PNGs with identical prompts to the direct
runtime and gateway. It checks semantic answers, JSON/SSE usage and invalid-image HTTP
errors for Chat, Responses and Messages. It sends paid requests when the gateway is paid;
it is not part of offline CI. It logs only classification, status, usage and request IDs.
Use an approved vision model and an owner-only file containing the gateway key:

```sh
uv run python live-runner/tests/probe_vision.py \
  --direct http://127.0.0.1:18100 --gateway http://127.0.0.1:18002 \
  --model qwen3.8-27b --key-file /private/path/test-key \
  --output live-runner/local/vision-results.json
```

The default pause between gateway submissions is 16 seconds for this single-slot test
setup's discovery refresh; it does not retry inference and is not a throughput test.
Offline HTTP tests separately assert image-byte preservation, usage/charges, backend
rejection and request-size limits without GPU access.

[Recorded image acceptance](vision-acceptance-2026-10-27.md) covers the exact pinned
vLLM/Qwen configuration and paid accounting; other modalities remain unverified.

[Metadata identity acceptance](metadata-identity-2026-10-27.md) verifies ordinary/proxy
transport and direct/aggregated discovery with an unmodified node.
