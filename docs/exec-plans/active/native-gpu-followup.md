# Native GPU follow-up

The operator authorized sequential vLLM, SGLang and Ollama tests using Qwen3.8-27B on
the RTX 5090. Model data stays in external named volumes. Publishing is restricted to
NE-Build-Track infrastructure; no upstream Livepeer mutation is authorized.

Acceptance: test the built node/gateway images with real discovery and HTTP transport;
verify nested/required/additional-property/local-reference schemas, conflicting prompts,
streamed arguments and tool-result follow-ups; distinguish transport/accounting correctness
from model/runtime schema guarantees. Exercise concurrency, disconnect, timeout and backend
recovery. Record exact model/image/quantization identities and sanitized results.

The user authorized the CloudSPE payer and Xode 3c7f orchestrator keys with no spending
cap. Reuse the already running CloudSPE signer/Clearinghouse, with an isolated $1 test
allocation, instead of starting a duplicate signer. A private local Xode paid node handles
the acceptance route. Four paid calls (success, stream, rejection, disconnect) have
observed wholesale costs; on-chain winning-ticket redemption is tracked separately.

- [x] Build local corrected node and gateway images.
- [x] Run the Qwen3.8 matrix separately on each backend.
- [x] Research strict-tools/combined-output runtime limitations.
- [x] Probe capacity and failure recovery.
- [x] Verify paid deployment prerequisites and run acceptance where available.
- [x] Record evidence and remaining release/publishing work.

- [ ] Observe finality of all persisted winning-ticket redemptions.

Evidence: [Qwen3.8 report](../../../live-runner/tests/qwen38-followup-2026-10-27.md).
Corrected images are published under `native-gpu-20261027`; the node lock is updated.
GPU services are stopped and model volumes retained. The private paid node remains
running with its inference runner idle; a bounded observer records redemption finality.
