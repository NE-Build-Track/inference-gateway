# Native inference forwarding

Status: implementation verified offline; deployment acceptance and review remain open.
Supported backend families are vLLM, Ollama and SGLang. Live verification remains deferred
at the user's request. The development stack stays stopped. Publish only to NE-Build-Track
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
- [ ] Exact runtime/GPU/node and paid-path acceptance, explicitly deferred.

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
pins Core `0d0b532`; Core pins SDK `cd6ed5e`.

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
