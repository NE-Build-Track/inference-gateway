# vLLM release acceptance

Ship one supported configuration before expanding runtime guarantees: Qwen3.8-27B AWQ
on pinned vLLM, with the gateway preserving native requests and observing usage. SGLang
and Ollama remain experimental. Existing volumes and unrelated remote GPU host services must survive
cleanup. Only NE-Build-Track publication is authorized.

Acceptance:

- Cancel three occupied single-slot streams; reject overlap without inventing capacity,
  then accept new requests within the configured discovery/cleanup window.
- Complete ten sequential calls and recover after a runtime restart.
- Create a fresh gateway database using the published gateway's normal entry point; verify
  paid unary success, SSE success and native HTTP rejection through the vLLM runner.
- Preserve runner identity through both direct and signer-based discovery, including proxy
  hosts; run node race tests and gateway checks.
- Record immutable image identities, source commits, paid reconciliation and chain finality
  separately. Do not call mined tickets finalized.
- Remove task-owned remote GPU host containers without removing any volumes or unrelated services.

Release review: node PR #1 requested preservation of runner_id through the signer.
Gateway PR #4 passes CI checks but SonarCloud requires security triage. Private runtime HTTP
and operator-selected renderer output paths are intentional deployment boundaries; findings
must receive explicit review rather than being hidden by blanket exclusions. No automatic
merge or production promotion is implied by functional acceptance.

Results: [release evidence](../../../live-runner/tests/vllm-release-2026-10-27.md).
Bounded recovery, paid gateway and cleanup checks are complete. The corrected node is
published and pinned. Remaining: review gates, all-ticket finality, live signer discovery
and fresh co-located Compose acceptance before production promotion.
