# Supported Livepeer inference runner bundle

This directory is the gateway-owned deployment contract for **vLLM, Ollama and SGLang**.
It contains exact integration code and digest-pinned upstream images, not copies of
those engines. One manifest generates runtime settings, readiness configuration and
Livepeer node registration, including `metadata.model_name`.

**Status:** candidate bundle with Qwen3.8 GPU acceptance and paid accounting evidence.
Complete runtime conformance, chain finality and production admission remain pending.
No image here is certified merely because its registry digest resolved. Generated
runners are deliberately `idle`, and metadata says `admission: candidate`; they do not
enter discovery as usable supply. Existing gateway traffic is unchanged.

## Layout

- [contract](contract/README.md): deployment/metadata schemas and fixed endpoint profiles.
- [images.lock.json](images.lock.json): immutable runtime, readiness and node image references.
- [vLLM](runtimes/vllm/README.md), [Ollama](runtimes/ollama/README.md),
  [SGLang](runtimes/sglang/README.md): model/parser configuration and limitations.
- [render.py](tooling/render.py): validates a manifest and emits a self-contained bundle.
- [readiness.py](tooling/readiness.py): observes model availability without proxying generation.
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
The templates intentionally contain invalid revision placeholders; the renderer refuses
them rather than substituting a mutable model branch or claiming a fake pin.

Set the exact model name, capacity, context limit and required tool/reasoning parsers.
Do not claim an endpoint because the family supports it in another version. Templates
request only the chat profile; add other profiles only to a candidate being tested.

```sh
uv run python live-runner/tooling/render.py \
  live-runner/local/deployment.json --output live-runner/generated/vllm

docker compose -f live-runner/generated/vllm/compose.json config --quiet
```

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

`release.json` records the manifest-derived deployment revision, all image digests and
integration code hashes. A model, image, parser, endpoint, context/capacity setting or
integration-code change changes the revision. `metadata.json` stays within 1,024 UTF-8
bytes for legacy compatibility. Metadata schema shape is checked by the shared Python
contract; semantic endpoint mappings are also checked by that contract.

Registry tags were resolved on 2026-10-27 into the checked-in lock. Generated Compose
never uses `latest`. Measured runtime versions and GPU behavior are recorded in the remote GPU report;
the image digest remains the authoritative pin. Lock updates require a new release identity
and acceptance evidence, not an automatic pull on service restart.

Do not edit generated files and keep the old evidence. Regenerate from the manifest.
Readiness only observes a currently visible model/health status (and Ollama loaded model
digest); it is not a certificate of tools, strict schemas, endpoint support or weight
integrity. HF weights are requested at the configured revision; runtime attestation is
not implemented. Admission must bind evidence to the complete deployed artifact.

## Next integration boundary

The gateway native path reads model/endpoint/profile metadata and observes usage while
preserving payload bytes. It requires operator-owned, expiring deployment approvals; see
[configuration](../docs/product-specs/native-inference.md). Validated routes retain their
existing selection and output guarantees. Generated registrations stay idle until deployment
acceptance and explicit promotion; metadata alone never grants native admission.

No automatic production promotion or approval-writing command is provided. Readiness is
not endpoint certification. The current integration pins NE-Build-Track review revisions;
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
