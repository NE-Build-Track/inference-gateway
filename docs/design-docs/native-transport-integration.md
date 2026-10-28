# Native transport integration boundary

Status: opt-in HTTP implementation verified offline. See the
[native contract](../product-specs/native-inference.md) for operator settings and limits.
Validated routes remain the default. Subsequent bounded GPU and paid evidence is linked
from [quality status](../QUALITY.md); release promotion remains separate.

## Ownership

| Component | Responsibility |
| --- | --- |
| `core/native.py` | Opaque HTTP request/response transport, separate from generation DTOs |
| `adapters/native_request.py` | Routing/accounting/state envelope and safe request headers; original bytes retained |
| `backends/native_selection.py` | Independent deployment approval before ranking/payment |
| `backends/native_livepeer.py` | Builder Core raw jobs, response ownership and usage reporting |
| `adapters/native_usage.py` | Bounded read-only JSON/SSE usage and terminal observations |
| `native.py` | Job binding, retail usage/outcome callbacks, reporting and cleanup |
| `adapters/native_http.py` | Deadline/disconnect ownership throughout ASGI delivery |
| `app.py`, `runtime.py` | Explicit route composition, auth, model catalog and operator configuration |

Native forwarding does not implement prompt templates, tool schemas, reasoning formats or
backend feature validation. The backend owns those semantics. The gateway retains admission,
authentication, permitted operations, statelessness policy and accounting. The raw transport
still goes through Builder Core; no separate HTTP client bypasses payment/session handling.

## Dependency review chain

The gateway pins Builder Core `2134e29`, the merge of
[Builder Core PR #55](https://github.com/NE-Build-Track/livepeer-builder-core/pull/55) into
`releases/m3` (28 October). Core sends the paid runner request itself (its D53), so native
transport lives in Core: bounded raw bodies, content type, validated headers, operation identity,
response header pairs and opt-in HTTP error delivery (its D56). Native error streams are not
decompressed, redirects are never followed, and no paid request is resent on a dropped connection.
Payment challenges and signer failures remain control flow. Core pins the SDK to upstream
`livepeer/livepeer-python-gateway` `2017a80`; no fork is pinned.

[SDK PR #6](https://github.com/NE-Build-Track/livepeer-python-gateway/pull/6) (`cd6ed5e`) and
Core PR #55's review revision `0d0b532` were the earlier chain; #6 is closed, superseded by #55.
An attempted upstream SDK PR creation was rejected by GitHub permissions; no upstream PR
was created and no upstream branch was pushed. The user's publication policy is NE forks only.

The initial inspection of Builder Core `629f1ab` found a parsed-JSON-only request and no
stream-header facade. Subsequent inspection confirmed SDK `76a1a1c` already included raw
payload support from PR #80. The review chain therefore extends existing SDK transport and
Builder Core facades; it does not reimplement paid raw-body transport. Selection already had
a public replacement interface and required no upstream selection rewrite.

## Verification and remaining work

Offline tests cover original bytes/unknown fields, all three endpoint usage formats,
retail ledger/job correlation, wholesale usage reporting, failed/incomplete Responses,
HTTP errors, expiration, authentication, deadlines, cancellation and cleanup. Builder Core
CI also verifies PostgreSQL storage and its container. The gateway package and local review
image build successfully. That initial offline slice did not start live inference; later
GPU/paid probes are recorded in quality status.

Remaining work is deployment acceptance and release review: review/merge the NE dependency
PRs, repin to accepted revisions as needed, test exact GPU/model/parser configurations,
verify node metadata/header propagation and real paid settlement, then promote accepted
runner revisions. Approval automation, hot revocation, shared-capacity multi-model layouts,
compressed payload observation and stateful APIs remain outside the current native profile.

The [cross-repository review](../references/repository-compatibility-2026-10-27.md) verifies
current M3 plus native patches and restores the separate discovery service in Compose.
Do not substitute an unpatched default branch for the native dependency pins.

Node PR #1 is no longer required: gateway discovery normalizes runner identity from
validated metadata and rejects conflicting IDs. SDK/Core native transport patches remain
required. See the [identity contract](../../live-runner/contract/README.md#runner-identity-without-a-node-patch).

The [28 October review](../references/repository-compatibility-2026-10-28.md) superseded
that overlay result for newer M3: Core now owns the paid runner call, so PR #55 had to port
native behavior onto that implementation. It did, and merged as `2134e29`, which the gateway
now pins. Native startup still rejects an installed Core that lacks the required request
fields or response-header facade.
