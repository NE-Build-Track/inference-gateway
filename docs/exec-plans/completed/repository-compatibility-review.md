# Related repository compatibility review

Review current gateway pins against the related NE repositories and preserve unrelated
local edits. Keep development services stopped; use only isolated test processes. Do not
publish upstream or upgrade payment dependencies on the basis of version numbers alone.

Acceptance: baseline gateway checks; compare latest M3 with/without native patches in
isolated checkouts; check discovery/auth/payment boundaries and deployment configuration;
fix gateway-owned drift with regression coverage; record immutable references and limits.

Findings: current pins pass. Latest M3 lacks the unmerged native PR and fails native tests;
latest M3 plus the three native commits passes the full gateway suite and strict typing.
Only pyproject pin/JWT-dependency conflicts needed resolution in the disposable overlay.
No shared Core checkout or its staged documentation was modified.

Gateway deployment omitted LPB_DISCOVERY_URL and the standalone discovery service. Restore
that wiring using the exact same image as the gateway, remove signer-owned discovery, and
fail startup before constructing the engine when discovery is missing. Test real discovery
CLI preservation of runner ID, proxy URL and metadata using an isolated loopback fixture.

Remaining before dependency promotion: review/merge Core native PR with current M3 and
retain the SDK native pin; deploy and validate the exact resulting artifacts. This review
does not re-certify GPU inference or paid finality and does not restart the dev stack.

Completed review: current and latest-M3-plus-native suites pass 483 tests; typing,
Compose, packaged discovery CLI and local image build pass. Newer Clearinghouse boundary
tests pass. Findings and remaining upgrade gates are recorded in the
[compatibility report](../../references/repository-compatibility-2026-10-27.md).
