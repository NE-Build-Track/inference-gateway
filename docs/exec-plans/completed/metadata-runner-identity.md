# Runner identity without node PR #1

Acceptance: generated runner metadata carries the registration ID. Discovery normalizes a
missing top-level ID from validated metadata; conflicting IDs fail closed. Existing top-level
IDs remain compatible. Native approvals still bind origin, runner, app, model, revision,
endpoint and expiry, and transport URLs stay unchanged. No URL-based identity guessing.

Verify direct and aggregated discovery against an unpatched node, plus paid-stream/error
accounting contracts. Repin the node only after those checks pass. Keep development stacks
stopped and preserve all volumes. Publish only to NE-Build-Track. Closing node PR #1 is the
user's action; no changes to the node source are required.

Completed: 500 offline tests and 36 real unpatched-node checks pass, including direct
and aggregated discovery, proxy transport, usage/retail accounting and native errors.
Paid-attempt lifecycle tests use simulated signing evidence. New live GPU/settlement
acceptance remains a deployment gate, not a node PR dependency.

Evidence: [metadata identity](../../../live-runner/tests/metadata-identity-2026-10-27.md).
