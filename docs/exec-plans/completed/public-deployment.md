# Standalone public deployment

## Outcome

Publish Inference Gateway under `NE-Build-Track/inference-gateway`, with an MIT
license and contribution, conduct, and private security-reporting guidance.
Rename the Python distribution and import package to `inference-gateway` and
`inference_gateway`. Update imports, executable entry points, containers and examples.

## Deployment decisions

Keep independent Compose projects in `deploy/livepeer/gateway` and
`deploy/livepeer/orchestrator`. The gateway owns its payment services and state.
The orchestrator template pairs the pinned Livepeer node with local vLLM, with
explicit model, pricing, origin and onchain settings. No sibling checkout or
external application network is required. Build upstream services from public,
pinned Git revisions. Operators supply credentials, funding and GPU capacity.

Remove the former application overlay and references from the current source tree.
Retain Git history, immutable accounting records and payment identities. Preserve
old state volumes as backups when moving the running services to their own network
and volumes. Never commit secrets or generated operator configuration.

## Verification

- All 132 offline tests, lint, formatting, strict source typing and documentation checks passed.
- Wheel/source package and gateway container builds passed after the import rename.
- Container smoke passed for both protocols in JSON and SSE modes.
- Compose configuration and offchain/onchain renderer checks passed.
- HTTP verification confirmed all 14 existing live acceptance records and exact cost totals.
- The running gateway imports `inference_gateway` and uses its own Compose network.

## Limits

The new orchestrator template has not run GPU inference or onchain payments on this
machine. The retained live acceptance evidence used the previously configured
remote Livepeer orchestrator. See the quality status for dated results.
