# Remote ComfyStream runners and synchronous network batches

The gateway routes through Builder Engine and Livepeer orchestrators. Model execution,
ComfyUI workspaces, checkpoints, GPU scheduling and runner processes belong to the
runner deployment. The gateway has no ComfyStream/Stream Pack Python dependency,
embedded execution path or direct runner URL configuration.

The gateway owns authentication, protocol translation, approved network selection,
accounting and synchronous HTTP delivery. For synchronous media batches it may persist
SQLite request receipts and authenticated response artifacts. Those records correlate
to real network job IDs; they do not form a local inference job queue. Restart recovery
marks incomplete outcomes unknown/failed and never repeats generation. Tenant-scoped
idempotency replays only saved responses, and retention defaults to 24 hours.

Version-two runner metadata adds ComfyStream identity, media endpoints and shared
capacity declarations without imposing text context-token limits. Version one remains
unchanged. External, expiring operator admission is required before any native request
is dispatched. Capacity enforcement remains the runner/orchestrator's responsibility.

Implemented candidate paths include synchronous image generation/editing, WAV speech,
transcription and transformation/music/mesh extension methods. They call the existing
NativeBackend/Builder Engine transport, retain original request bytes and preserve
provider response bodies. SQLite/artifact tests cover network dispatch, idempotency,
multipart normalization, restart recovery, owner checks and expiry. Existing chat,
Responses and Messages OpenAPI bodies/authentication/SSE descriptions are included.

Remaining acceptance: complete media schemas and SDK/error/limit/disconnect matrices;
network media-unit usage and retail pricing; async video/job lifecycle forwarding;
v2 deployment rendering; all-nine-model immutable runner-image acceptance and end-to-end
paid orchestrator tests. Registry billing currently refuses media dispatch rather
than inventing input/output tokens. Runner metadata alone is not certification.

Run the complete Linux `make check` and package build after these changes. Keep this
plan active until network acceptance passes. Production ComfyStream remains unchanged.
