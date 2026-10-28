# Metadata runner identity — 27 October 2026

Node PR #1 is no longer required. The gateway-owned bundle advertises its registration ID
as `metadata.runner_id`. Native discovery fills a missing top-level ID from that field;
conflicting IDs fail closed. Admission still requires independent operator approval, exact
origin/app/model/revision/endpoint matching, capacity, freshness and expiry. The advertised
transport URL is never rewritten or parsed to guess identity.

## Evidence

An unmodified node was built from `dfcf03370cab1f8a08c82914fd874e437f22268c`, without either
commit from node PR #1. Its real `/discovery` response omitted top-level runner IDs and
preserved the metadata IDs, as expected. Two ready offchain fixture runners used ordinary
`/apps/...` and proxy-template URLs. Tests exercised both direct node discovery and the
actual Builder Core `lpb discovery` aggregation process.

Each discovery path completed 18 HTTP checks: two runners × three native endpoints ×
unary success, SSE success and backend HTTP 400. All 36 checks passed, and request bytes
reached the fixture backend unchanged. Each path recorded 12 successful retail usage
charges and six failed requests without usage charges. Core's discovered runners contained
the normalized IDs before selection. The test used real node/SDK/Core transport and a
local fake inference backend; it did not run GPU inference or spend on the paid network.

Offline real-Core tests additionally verify one paid attempt and the correct job outcome
for successful streams and preserved HTTP errors with metadata-only identity. Payment
signing evidence is simulated in these tests; this is not new live settlement acceptance.
Other regressions cover missing/invalid/conflicting IDs, no approval, legacy top-level IDs,
no URL guessing, validated-route isolation and the full legacy metadata-size allowance.

`make check` passes 500 tests, lint, formatting, typing and documentation. All three runner
families render and their Compose configurations validate. The gateway image's packaged
normalizer is also smoke-tested. The isolated node/discovery/backend processes are removed;
the development stack and existing payment/finality containers remain stopped. No volumes
were removed or GPU services started.

## Rollout

Use the `native-metadata-20261027` candidate images and the current node lock. The node
candidate is built from unmodified source; its tag does not imply a node fork patch.
Deploy the new gateway first. Existing valid top-level identity registrations continue to
work. Then regenerate runner configuration so metadata carries the ID, deploy the unpatched
node configuration, and issue operator approvals for the newly generated deployment revision.
Older gateway images reject the new metadata field, so do not mix old gateways with newly
rendered metadata. SDK/Core native transport dependencies remain required.

Historical GPU/paid reports retain their original patched-node evidence. This change removes
that patch dependency through gateway normalization; it does not rewrite those historical
runs as unpatched-node or new-image live acceptance. Live paid/GPU checks on the final
combined deployment remain part of production promotion.

## Published manifests

- Node: `registry.livepeer.tools/ne-build-track/inference-node@sha256:de5fb0bf5118cd2171a64c4cae0038dc7d259030c64c2df05f2f613e617f6a30`.
- Gateway: `registry.livepeer.tools/ne-build-track/inference-gateway@sha256:56314df83a7a1a589b40d548a9bf4b6099a42662f77f067a5133550bd0052289`.

Both use candidate tag `native-metadata-20261027`; existing `v0.0.1` tags are unchanged.
