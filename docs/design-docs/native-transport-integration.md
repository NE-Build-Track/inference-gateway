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

All branches and PRs are in NE-Build-Track forks:

- [SDK PR #6](https://github.com/NE-Build-Track/livepeer-python-gateway/pull/6), commit
  `cd6ed5e`: validated runner protocol headers and opt-in non-402 error streams. Payment
  challenges remain control flow. Native streams disable redirects, implicit connection
  retries and decompression; original bytes and repeated response headers are exposed.
- [Builder Core PR #55](https://github.com/NE-Build-Track/livepeer-builder-core/pull/55),
  commit `0d0b532`, targets `releases/m3`. It adds bounded raw bodies, content type, validated
  headers, operation identity and opt-in HTTP error delivery. Completed native HTTP errors
  record failed jobs, including when paid; interrupted paid streams preserve uncertainty.

The gateway pins those immutable review revisions. They are not merged release claims.
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
