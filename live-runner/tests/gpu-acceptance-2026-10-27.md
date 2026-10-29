# Remote GPU acceptance — 27 October 2026

This run exercises the native gateway path against real GPU engines, with a local
Livepeer orchestrator, readiness observer, Builder Core and gateway. It is an offchain
integration test, not a paid settlement test or a general model-quality certification.
Results apply to these exact images, model revisions and configurations.
The [sanitized evidence](gpu-acceptance-2026-10-27.json) records image/command identities,
the 96 direct/gateway baseline checks, 24 conflicting-prompt checks, lifecycle results
and accounting audits. The test services and SSH tunnel were stopped afterward; the
GPU returned to 2 MiB used and persistent model volumes were retained.

## Deployment

The remote host has an NVIDIA RTX 5090 with 32 GB VRAM, driver 580.178.04. The operator stopped
the pre-existing GPU workload before these tests. Only the test runtime containers use
the GPU, one at a time. Unrelated services were left running.

The runtime binds remote loopback port 18100. An SSH tunnel exposes that port on local
loopback. The local readiness observer listens on 18080, the offchain node on 18935,
and the gateway on 18001. None of these test endpoints has public ingress.

| Component | Version / identity |
| --- | --- |
| vLLM | 0.32.0; image digest in the accompanying evidence |
| SGLang | 0.5.22; image digest in the accompanying evidence |
| Ollama | 0.40.11; image digest in the accompanying evidence |
| vLLM/SGLang model | `Qwen/Qwen2.5-0.5B-Instruct`, HF commit `7ae557604adf67be50417f59c2c2f167def9a775` |
| Ollama model | `qwen2.5:0.5b`, manifest `sha256:a8b0c51577010a279d933d14c2a8ab4b268079d44c5c8830c0a93900f1827c67`, Q4_K_M |
| Limits | 4,096-token context; one advertised runner slot |
| Tool parsers | vLLM `hermes`; SGLang `qwen25`; Ollama model-native template |
| Builder Core | NE review revision `0d0b53205cd4047e9151e8a67169585af38686c8` |
| Python gateway SDK | NE review revision `cd6ed5e539b3e98f26f04cd316f9c99e4bec952a` |
| Local node | Source `dfcf033` plus the local `runner_id` discovery patch; binary hash in evidence |

Runtime image digests come from [the lock](../images.lock.json). Test topology has a
separate revision from the standard generated bundle: the GPU is remote, the node is
a locally built binary, and test registrations are deliberately ready with short-lived,
isolated approvals. These approvals do not certify or promote production supply.

The gateway uses the real Builder Core services and SDK HTTP transport, HTTP discovery,
separate durable SQLite job and retail databases, registry authentication and test prices.
It uses no fake backend, payment signer or funded allocation. A test price of $1 per
million input tokens and $2 per million output tokens makes charge arithmetic testable;
these prices are fixtures, not a commercial quote.

## Results and scope

Each endpoint baseline is tested both directly and through the gateway. Successful
responses require terminal usage. Tool/JSON samples are checked for the expected object,
not merely HTTP 200. A second strictness probe asks for a value that conflicts with a
schema enum. These small probes demonstrate specific behavior; they do not prove every
JSON Schema construct, tool selection policy or parser/model combination.

| Behavior | vLLM | SGLang | Ollama |
| --- | --- | --- | --- |
| Model catalog identity through gateway | Pass | Pass | Pass |
| Chat Completions, unary and SSE | Pass | Pass | Pass |
| Stateless Responses, unary and SSE | Pass | Pass | Pass |
| Anthropic Messages, unary and SSE | Pass | Pass | Pass |
| Strict JSON and local `$ref`, Chat/Responses | Pass | Pass | Pass |
| Strict tool samples and local `$ref`, Chat/Responses | Pass | Pass | Samples conform, but strict enforcement fails conflicting-prompt probe |
| Conflicting-prompt strict tool / JSON checks | Pass / Pass | Pass / Pass | Fail / Pass |
| Tools plus structured output, Chat/Responses | Pass | Unsupported Response: HTTP 400 | Unsupported Response: HTTP 200 without the required function call |
| Usage, job correlation and one retail charge per measured request | Pass | Pass | Pass after Messages usage fix |
| Backend error status/body preservation, no charge without usage | Pass | Pass | Pass |
| Stream disconnect releases node capacity | Pass | Pass | Pass |

“Unsupported Response” is an acceptance finding. Native forwarding preserves the backend's
status/body; it does not invent a new error or silently add gateway schema enforcement.
Ollama's combined-output responses are therefore still forwarded as HTTP 200 and billed
for measured generation. Applications requiring strict tools must not treat that runtime
as certified merely because basic tool samples pass. Current endpoint-level approvals do
not enforce feature-level admission; operator/client policy must account for this gap.

## Fixes found by live testing

1. vLLM's optional `prompt_tokens_details` was null by default. The reader now accepts
   absent/null optional token-detail objects, while rejecting malformed objects/counts.
   The renderer enables `--enable-prompt-tokens-details` for vLLM and
   `--enable-cache-report` for SGLang. Missing cache detail means zero *reported* cache
   tokens, not a claim that no cache was used.
2. Ollama Messages SSE initially reports provisional input usage, then supplies final
   input/cache counts in `message_delta`. The reader now applies cumulative final fields
   over `message_start` instead of keeping preliminary input counts. Fragmented-stream
   regression coverage uses the observed 7 provisional versus 11 uncached + 24 cached
   final input tokens. Provider bytes remain unchanged.
3. The locked node image omitted `runner_id` from discovery. Native admission correctly
   rejected it. The local node patch exposes the registered identity explicitly and tests
   its JSON serialization. Admission remains fail-closed; no URL-derived ID fallback was
   introduced.

The node fix is in the sibling `livepeer-node` checkout on
`tasks/mz/discovery-runner-id`. **The node image in images.lock.json does not contain that
fix.** A reviewed node build, image publication, lock update and bundle rerender are still
needed before the standard bundle can reproduce this working native path. These test
results must not be attributed to the existing node v0.0.1 image.

## Persistence and repeat runs

GPU-host model data lives in Docker named volumes created independently of any Compose
project:

- `ne-gpu-acceptance-hf`: shared HF cache for vLLM and SGLang, mounted at
  `/root/.cache/huggingface`. SGLang confirmed it reused the pinned snapshot without a
  second download.
- `ne-gpu-acceptance-ollama`: Ollama manifests and blobs, mounted at `/root/.ollama`.
- `ne-gpu-acceptance-vllm-compile`: vLLM compile cache at `/root/.cache/vllm`.

Do not remove these volumes when replacing test containers. A Compose file taking over
these test deployments should declare the existing volumes with `external: true` and
these exact names. Ollama readiness checks the loaded model's manifest digest; loading
must precede admission. A mutable tag alone is insufficient for repeatable acceptance.

Local manifests, topology artifacts, databases and probe scripts are retained in ignored
`live-runner/local/gpu-acceptance` and `live-runner/generated/gpu-{vllm,sglang,ollama}`.
The key files there are local test secrets and must not be committed. Sanitized evidence
contains statuses, usage counts and validation outcomes, never prompts, tool arguments,
credentials or raw backend error bodies. Reruns must wait for the gateway model catalog,
not just runtime health, because discovery refresh is asynchronous.
When a one-slot runner is sampled while busy, cached zero capacity can cause temporary
503s after it becomes free. The engine's default discovery interval is 15 seconds.
Acceptance probes waited for catalog availability before dispatch; this is not evidence
of production concurrency readiness or a reason for clients to replay paid requests.

Gateway `make check` passes 472 tests, lint, formatting, strict typing and documentation.
The node passes `go test -race ./...` with umask 022. The initial run with umask 002
failed an existing signer directory-permission assertion (expected 0755, got 0775);
no signer code or test was changed to obtain the passing run. Changes remain local,
uncommitted and unpublished at the end of this acceptance session.

## What this run does not establish

Paid challenges, signer/Clearinghouse settlement, concurrency/load limits, crash/restart
accounting, retention auditing, multi-node failover, deadline fault injection and production
network/TLS configuration remain unverified here. Images, audio, reasoning, hosted tools,
large models and broader schema constructs are outside this small text-model matrix.
Strict tools were tested on Chat and Responses; Anthropic strict-tool semantics need a
separate acceptance matrix. The unit suite covers additional failure paths offline.
