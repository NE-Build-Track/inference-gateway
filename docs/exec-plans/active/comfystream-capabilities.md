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

The five-model catalog (FLUX, BiRefNet, Whisper, ACE-Step and TripoSR) is deployed on
eliteprd1 at USD 0.02 per request and appears in production Engine API discovery.
Qwen and Wan are excluded. Gateway branch main was merged at `6dd7bb1`; all 657 tests
and the v0.2.2 build passed before the admission/probe tooling was added.

`scripts/prepare_catalog_admission.py` compiles reviewed deployment/discovery/OpenAPI
exports into exact model routes, expiring approvals and a deployment-bound Core
schema registry. `scripts/test_paid_catalog.py --run` submits five single-attempt
production Engine API requests using an existing bearer credential file, persisting
operation references before dispatch. A failed or uncertain call stops the run;
existing reports cannot be reused to repeat generation. It records job IDs and
actual costs without fabricating settlement or token counts. Cost reconciliation is
still required after serving.

The reporting console uses OIDC. Its saved older LPK admin key returns 401 from the
production engine; its current reader is read-only. No roles or allocations have
been changed to enable testing. Paid acceptance awaits an authorized Builder
credential/session. Core/console schema changes are candidate source changes pending
their required human review. Keep this plan active until network acceptance passes.
