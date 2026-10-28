# vLLM release acceptance — 27 October 2026

This follow-up narrows support to the Qwen3.8-27B AWQ configuration in the
[three-engine report](qwen38-followup-2026-10-27.md). SGLang and Ollama remain experimental.
The published gateway candidate is unchanged; this run uses its normal application entry
point for paid acceptance and a fresh retail/engine database. The orchestrator's existing
payment volume is retained so pending redemptions survive.

## Capacity contract

The tested node advertises one slot. Overlap is rejected with 503 while that slot is busy.
Three cancellation cycles on the final node candidate returned to successful inference
after 1.763, 1.558 and 1.432 seconds. Immediate post-cancel probes still observed zero capacity and 503. Ten sequential
requests then succeeded. This resolves the earlier ambiguous snapshot: capacity is released
asynchronously, not leaked. Discovery is sampled availability, not a reservation; a catalog
entry is not a promise that a simultaneous inference request can obtain capacity. Do not
retry paid generations automatically to hide temporary capacity rejection.

These bounded probes are not a production throughput or multi-orchestrator load test.

## Fresh paid gateway

Unary Chat and streaming Responses returned 200; invalid input preserved backend HTTP 400.
Each reached a paid attempt. Successful generations each measured 15 input and 3 output
tokens and were charged; the rejected request retained unknown usage and no retail charge.
An initial request during a temporary test-node configuration error returned 503 without a
job or paid attempt. Correcting the local TOML restored admission; no application patch was
needed for this deployment error.

## Release review boundaries

Node discovery identity also has to survive the signer's typed discovery collector. The
follow-up patch retains runner_id there and tests proxy-host discovery, in addition to direct
orchestrator discovery. The `native-vllm-20261027` node includes that signer fix. Paid testing used the previous
node candidate through direct discovery; the new node passed the same offchain recovery
checks and the full Go race suite. Live signer-discovery acceptance remains separate.

SonarCloud reports private HTTP listeners/URLs and the operator-selected renderer output
directory. The bundle's runtime HTTP is on its private container network; its node publishes
only on loopback by default. Public exposure needs an authenticated TLS edge. Renderer output
is an operator CLI choice, never an inference request field; run it only with reviewed paths
and manifests. These boundaries are documented for review; the failed quality gate is not
silently waived. PR review and production promotion remain separate from these tests.


## Restart, accounting and cleanup

Runtime health returned 45.537 seconds after restart; gateway inference succeeded at
57.041 seconds, including discovery refresh. Three paid requests reconciled observed
expected-ticket costs of $0.000999048939331017 each (about $0.002997 total). This is
wholesale evidence, not finalized redemption. Two of the accumulated eleven winning
tickets were confirmed at the recorded snapshot; nine remained pending.

All six task-owned inference containers were removed from ai2-rig. Its five pre-existing
services remain running. All fourteen pre-existing/model volumes remain, including the
three NE model/compile caches. GPU memory returned to 2 MiB. Local test gateway/node
containers, readiness server and SSH tunnel were stopped/removed. Only the private paid
node (runner idle) and bounded finality observer remain for ticket redemption; unrelated
local services were not changed.

## Published candidates

The existing v0.0.1 tags are unchanged. Both images have the tag `native-vllm-20261027`:

- Node: `registry.livepeer.tools/ne-build-track/inference-node@sha256:6042f62ea94213c700c57e4d7e1ae1fab8bfb07f4055a140552708e6eedd4561`.
  The binary reports commit `e75e5b6a00fac471f0dce561dea4d564a651513b`.
- Gateway: `registry.livepeer.tools/ne-build-track/inference-gateway@sha256:cfd1bcdca6cbb8eb01eecee079f2636c9f432ed573038a3e8e535867e472548f`.
  This is the previously tested application artifact; this follow-up changes the node and
  evidence/bundle pins, not gateway application code.

Use the [bundle instructions](../README.md) to render from a reviewed manifest and the
[configuration contract](../../docs/product-specs/native-inference.md) to provision the
native route and expiring approval. Deployment revision changes with the node lock;
regenerate configuration and approve the new exact revision. Do not reuse a historical
approval against a newly rendered deployment. The split-host acceptance topology is not
an end-to-end test of a fresh, co-located Compose deployment.

Review gates and the outstanding chain finality prevent calling this a production release.
