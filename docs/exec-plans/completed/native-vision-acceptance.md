# Native vision acceptance

Acceptance: preserve image-bearing JSON through Chat, Responses and Messages in unary and
streaming mode; observe backend token usage and exactly one retail charge. Compare real
vLLM direct and paid-gateway answers for two different synthetic images using an identical
prompt that does not reveal the expected answer. Verify an invalid image is not billed as
successful inference. Keep raw images, prompts and responses out of logs/evidence.

Use the pinned Qwen3.8-27B AWQ model, whose exact configuration contains a vision encoder,
and existing persistent GPU volumes. Remove task containers and tunnels afterward, retaining
all volumes. Record model/runtime/endpoint-specific results; do not infer audio/video/PDF
support from successful image tests. Only NE-Build-Track publication is allowed.

Completed: all 30 live image checks and 15 paid accounting audits pass. Offline CI passes
481 tests. No gateway application change was required. The probe classifier was corrected
to use streaming deltas only. Remote GPU host test containers were removed, all volumes retained.

Evidence: [image acceptance](../../../live-runner/tests/vision-acceptance-2026-10-27.md).
Other input modalities and overall release/finality gates remain separately tracked.
