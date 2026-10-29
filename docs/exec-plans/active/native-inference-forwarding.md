# Native inference forwarding

Status: offline checks and limited offchain GPU acceptance verified; release-artifact
acceptance, paid-path acceptance and review remain open.
Supported backend families are vLLM, Ollama and SGLang. The user has authorized isolated
GPU acceptance on a remote RTX 5090, with the runner/node and gateway local. Paid-path
acceptance remains deferred. The existing development stack stays stopped. Publish only to NE-Build-Track
forks; do not push branches or create PRs in upstream Livepeer repositories.

## Acceptance and implementation

- [x] Shared candidate runner metadata, endpoint/usage profiles, pinned manifests and readiness.
- [x] Public raw transport boundary separate from the validated generation event contract.
- [x] Original request/response bytes and safe protocol headers; unknown generation fields survive.
- [x] Explicit native route mode, model/endpoint/deployment admission and expiry before payment.
- [x] Authentication, retail price snapshot, job binding and existing catalog price filtering.
- [x] Read-only unary/SSE usage observation and wholesale usage reporting without replaying inference.
- [x] Errors, incomplete/failed responses, deadline, cancellation and generator cleanup.
- [x] Existing validated routes retain their default behavior.
- [ ] Review/merge NE dependency and gateway PRs; repin accepted revisions as needed.
- [x] Limited offchain GPU matrix for three runtime families, using a patched local node.
- [ ] Publish/repin the node discovery fix and verify the complete released runner bundle.
- [ ] Paid-path acceptance, still deferred.

GPU acceptance compares direct runtime calls with calls through the local node, real
Builder Core/SDK transport and gateway. It checks catalog identity, unary/SSE usage,
strict schemas, local references, tools combined with structured output, and retail/job
accounting. Backend rejections are recorded as Unsupported Response, not translated into
successful responses. Split-host test artifacts have their own deployment identity and
temporary test approvals; they do not promote the standard candidate bundle.
See the [GPU report](../../../live-runner/tests/gpu-acceptance-2026-10-27.md) for runtime
limitations, persistent caches and the exact scope. Gateway checks now pass 472 tests.

The [native contract](../../product-specs/native-inference.md) defines current behavior.
The [design](../../design-docs/native-inference-routing.md) records the direction and wider
acceptance targets; the [integration boundary](../../design-docs/native-transport-integration.md)
records implementation ownership and the dependency review chain.

## Verification

Runner scaffold: all three generated Compose configurations validate using explicit fixture
model pins. No model downloads or services were started. See the
[scaffold record](../completed/live-runner-scaffold.md).

Gateway: `make check` passes 465 tests, plus lint, formatting, typing and documentation.
Tests include native HTTP bytes, accounting, failures, expiry, auth, deadlines and ASGI
client disconnects. Wheel/sdist and local `ne-build-track/inference-gateway:native-review`
image build successfully. This image has not replaced a published release.

SDK [NE PR #6](https://github.com/NE-Build-Track/livepeer-python-gateway/pull/6) passes 346
tests. Builder Core [NE PR #55](https://github.com/NE-Build-Track/livepeer-builder-core/pull/55)
targets releases/m3 and passes 972 local tests plus CI PostgreSQL/container checks. Gateway
pinned Core `0d0b532` and SDK `cd6ed5e` at the time; it now pins Core `d3df806` with the upstream SDK
(see the [upstream repin plan](upstream-repin-publication.md)).

## Decisions and remaining limits

- Require chat stream usage opt-in instead of silently rewriting the body.
- Require Responses `store: false`; reject stateful/background/query extensions.
- Independent operator approval binds origin, runner, app, model, endpoint and deployment
  revision. Expiry is checked at selection; configuration reload/revocation requires restart.
- Metadata remains a candidate declaration; generated runner registrations stay idle until
  explicit deployment acceptance and promotion. No automatic certification command is shipped.
- Backend strictness belongs to native mode; existing gateway strict validation stays in
  validated mode. Native incomplete/failed provider payloads remain visible and retain usage.
- Reject encoded responses pending a decoding/observation profile. Shared physical capacity,
  automatic evidence refresh and stateful APIs need separate work.

The [GPU follow-up](native-gpu-followup.md) records the later 472-test check, three-engine
Qwen3.8 matrix, four paid calls and published NE candidate images. The node lock now
includes the discovery identity fix. Chain finality and production admission remain open.

Superseding the earlier node-patch requirement: [metadata runner identity](../completed/metadata-runner-identity.md)
normalizes IDs in this gateway. The current node lock is unpatched; node PR #1 is optional.
