# Supported Livepeer inference runner bundle

This directory is the gateway-owned deployment bundle for **vLLM, Ollama and SGLang**, under
[runner contract v1](../docs/product-specs/runner-contract.md). It contains exact integration code
and digest-pinned upstream images, not copies of those engines. One manifest generates runtime
settings, readiness configuration and, for each registration, the standard's `runner.toml`, its
envelope and the node's `[[Runners]]` block.

**Status:** candidate bundle with Qwen3.8 GPU acceptance and paid accounting evidence.
Complete runtime conformance, chain finality and production admission remain pending.
No image here is certified merely because its registry digest resolved. Generated blocks say
`Status = "ready"`, as the standard's tool writes them, but the bundle's node is offchain and binds
loopback, so a candidate serves no network traffic until an operator moves it. Configuration is
never certification: approvals stay with the gateway operator.

## Layout

- [contract](contract/README.md): the deployment schema and how registrations follow the standard.
- [images.lock.json](images.lock.json): immutable runtime, readiness and node image references.
- [vLLM](runtimes/vllm/README.md), [Ollama](runtimes/ollama/README.md),
  [SGLang](runtimes/sglang/README.md): model/parser configuration and limitations.
- [Clef-Flash](runtimes/clef/README.md): a decision model on each backend, configured by hand.
- [render.py](tooling/render.py): validates a manifest and emits a self-contained bundle, checked as the node reads it.
- [readiness.py](tooling/readiness.py): answers ready only after a self-test of every advertised profile.
- [orchestrator](orchestrator/README.md): node configuration and admission boundary.
- [tests](tests/README.md): evidence and remaining runtime acceptance.

## Prepare a candidate

From the repository root, copy the desired template into ignored local storage:

```sh
mkdir -p live-runner/local
cp live-runner/runtimes/vllm/manifest.example.json live-runner/local/deployment.json
```

Edit the copied manifest. Supply the actual immutable model revision: a 40-character
Hugging Face commit for vLLM/SGLang or the installed model manifest SHA-256 for Ollama.
The templates intentionally contain invalid revision and price placeholders; the renderer refuses
them rather than substituting a mutable model branch, a fake pin or a price the node doesn't charge.
Set `price` to the wholesale rate in USD, per `hour` of runner time or `fixed` per call.

Set the exact model name, capacity, context limit and required tool/reasoning parsers.
Set `reasoning` to `on` for a model that reasons unless asked not to, such as Qwen3.8, or `off`
for one that answers without reasoning; the envelope declares it, and gateways select by it.
`on` requires a reasoning parser, which keeps the thinking out of the answer's text.
Do not claim an endpoint because the family supports it in another version. Each
`manifest.example.json` requests only the chat profile; add other profiles only to a candidate
being tested. A manifest that lists chat endpoints and `systemone` renders two registrations,
`<runner_id>-chat` (App `chat`) and `<runner_id>-decision` (App `decision`), sharing one
`capacity_group`, because an App ID names one capability.

```sh
uv run python live-runner/tooling/render.py \
  live-runner/local/deployment.json --output live-runner/generated/vllm

docker compose -f live-runner/generated/vllm/compose.json config --quiet
```

`--output` must stay inside the directory you run it from: the bundle overwrites files there, so a
path that resolves elsewhere is refused before anything is written.

Rendering and Compose validation do not start services, download model weights or make
paid requests. The optional local candidate launch below uses the GPU and may download
weights; this work has not been run as part of the offline implementation:

```sh
docker compose -f live-runner/generated/vllm/compose.json up -d
```

Runtime and readiness ports are private to the Compose network. The offchain node binds
host loopback port 8935. Change the renderer/contract deliberately if another binding is
needed; do not expose runtime management APIs through a wildcard public proxy. The
`public_url` must describe the node origin clients can actually reach.

The bundle is initially Linux amd64 with one NVIDIA GPU. Ollama CPU-only configurations,
other accelerators, multiple nodes and shared-capacity multi-model layouts need separate
acceptance; do not infer their support from upstream multi-platform image manifests.

## Release identity and drift

Each registration's `deployment_revision` follows the standard's recipe: the runtime and readiness
images, the model's source and revision, the registration's profiles, and the settings that change
behavior (runtime, context length, tool and reasoning parsers). The URL, capacity, price and the
bundle's own code stay out, so moving the node or raising capacity keeps approvals, and changing the
model, an image or a parser voids them. `release.json` records each revision with the image digests
and integration code hashes. The renderer checks every block as the node reads it, and the standard's
`runner-contract` tool recomputes each revision from the written `<id>.runner.toml`:
`runner-contract check <id>.block.toml --manifest <id>.runner.toml` checks one registration, and
`runners.toml` holds every block for the node.

Registry tags were resolved on 2026-10-27 into the checked-in lock. Generated Compose
never uses `latest`. Measured runtime versions and GPU behavior are recorded in the remote GPU report;
the image digest remains the authoritative pin. Lock updates require a new release identity
and acceptance evidence, not an automatic pull on service restart.

Do not edit generated files and keep the old evidence. Regenerate from the manifest.
Readiness answers 200 only after one small request to each advertised endpoint returns a served,
usage-bearing answer, and returns to 503 when the model disappears; it is not a certificate of
tools, strict schemas or weight integrity. HF weights are requested at the configured revision; runtime attestation is
not implemented. Admission must bind evidence to the complete deployed artifact.

## Next integration boundary

The gateway reads each runner's envelope and observes usage while preserving payload bytes.
Native routes need an operator-owned, expiring approval with a passing conformance report for every
runner; see [admission and approvals](../docs/product-specs/runner-contract.md#admission-and-approvals).
Validated routes select candidate runners without one. The envelope alone never grants admission.

No automatic production promotion or approval-writing command is provided. Readiness is
not endpoint certification. This bundle's node is pinned in the [image lock](images.lock.json) and
moves when the next node image is published. The Compose deployments build `livepeer/node` at
`a4b68ed`, the candidate recorded in the [29 October repin](tests/m3-upstream-2026-10-29.md).
Chain finality and production admission remain pending.

## Initial supported configuration

Focus release acceptance on the tested Qwen3.8-27B AWQ vLLM configuration. SGLang and
Ollama remain experimental, with feature gaps in the reports. See the
[vLLM release follow-up](tests/vllm-release-2026-10-27.md) for candidate digests, recovery
behavior, paid accounting and outstanding release gates. This is a candidate, not an
automatic production approval.

## Remote GPU evidence

The [27 October offchain run](tests/gpu-acceptance-2026-10-27.md) tested these runtime
images with a remote GPU and local node/gateway. The subsequent
[Qwen3.8 follow-up](tests/qwen38-followup-2026-10-27.md) tests the corrected Docker images,
now published as NE candidates; the lock pins that tested node artifact. It also records
four paid calls, persistent volumes, exact configurations and runtime feature gaps.
Generated deployments still require their own explicit approval; these tests do not
automatically promote deployments or certify every backend feature.

## Node compatibility

The current bundle uses an unpatched node and names the registration ID in the envelope's
`runner_id`. Node PR #1 is no longer a dependency. See the
[identity contract](contract/README.md#runner-identity-without-a-node-patch) for conflict
handling. Older GPU reports describe the images used at that time;
the current lock and metadata-identity report supersede their node-patch requirement.
