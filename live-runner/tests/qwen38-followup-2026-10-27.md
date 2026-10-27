# Qwen3.8-27B native follow-up — 27 October 2026

The user requested Qwen3.8-27B on vLLM, SGLang and Ollama, one runtime at a time, with
persistent model storage. This extends the [small-model acceptance](gpu-acceptance-2026-10-27.md).
It tests corrected Docker images subsequently published as NE candidates; the runner lock
now references the tested node artifact.

## Configuration

The GPU is the same RTX 5090 with 32 GB VRAM. Full BF16 weights are not the tested
configuration. vLLM and SGLang use `cyankiwi/Qwen3.8-27B-AWQ-INT4`, pinned to HF commit
`e3a700906e6a73ba4290b1873dc2ba8198527ab5`, served as `qwen3.8-27b`. Both use the
`qwen3_coder` tool parser and `qwen3` reasoning parser, a 4,096-token context and a maximum
of four engine sequences; the local node advertises one slot. Ollama uses `qwen3.8:27b`,
Q4_K_M, manifest `sha256:aaee06c39dcf2437cde036998d960e1fc1494b8191be7cc9657d01e509097813`.
These are different quantizations, not bit-identical model comparisons.

Tests explicitly disable thinking for bounded text/schema probes (`chat_template_kwargs`
for vLLM/SGLang, `reasoning.effort: none` for Ollama, and `thinking.type: disabled` for
Messages). This is test configuration; the gateway preserves request bodies. Reasoning
quality/budgets are not certified by this run. Runtime image pins remain those in
[images.lock.json](../images.lock.json): vLLM 0.32.0, SGLang 0.5.22, Ollama 0.40.11.

The built local images are:

- Node: `ne-build-track/inference-node:gpu-followup`, image ID
  `sha256:361b96c5e5fd0915856145586ca96578287e20d4724ce9bbc17feb211ae985c3`.
- Gateway: `ne-build-track/inference-gateway:gpu-followup`, image ID
  `sha256:edaceee6bab5dc28da1490474aa79d5307b77b6ea6cf2a6ff2ef374da615b7b7`.

Published candidate tag: `native-gpu-20261027` (existing `v0.0.1` tags are unchanged).
Registry manifest digests:

- Node: `registry.livepeer.tools/ne-build-track/inference-node@sha256:be68e3776cf624949959482e2694bb73b5a4b0a8e7a251d25ad08bb04669e7c9`.
- Gateway: `registry.livepeer.tools/ne-build-track/inference-gateway@sha256:cfd1bcdca6cbb8eb01eecee079f2636c9f432ed573038a3e8e535867e472548f`.

Pulling these digests reproduces the tested image IDs above. The node source patch is
commit `29e069d1080924d4f079c685e2f4ee62053da390`,
[NE node PR #1](https://github.com/NE-Build-Track/node/pull/1). Images were built before
committing the tested working tree; source snapshot hashes are recorded in the JSON
evidence, not asserted as embedded image labels. The node includes the discovery-ID patch. Offchain gateway containers mount only a
composition fixture and writable test data: application code/dependencies come from the
built image. The paid gateway uses its normal module entry point and environment
configuration, without that fixture. Docker is rootless here; UID 0 in test containers
maps to the invoking host user and permits access to owner-only fixture files.

## Schema and conversation tests

Each extended matrix contains 32 calls: Chat/Responses × nested/local-reference schema ×
tool/JSON output × unary/streaming × direct/gateway. Prompts request conflicting enum
values, omitted required fields and extra properties. Outputs are checked against
Draft 2020-12 JSON Schema; streamed arguments are assembled before validation. Raw
prompts, arguments, responses and secrets are excluded from evidence.

| Extended result | vLLM | SGLang | Ollama |
| --- | --- | --- | --- |
| Nested/local-reference strict JSON | 16/16 pass | 16/16 pass | 16/16 pass |
| Nested/local-reference strict tools | 16/16 pass | 16/16 pass | 0/16 pass: 12 schema violations, 4 missing/invalid tool outputs |
| Chat tool-result follow-up | Pass direct and gateway | Pass direct and gateway | Pass direct and gateway |
| Responses tool-result follow-up | Pass direct and gateway | Pass direct and gateway | Pass direct and gateway |

This is a finite matrix, not proof of arbitrary schema enforcement. In particular,
Ollama's simple tool calls and follow-ups working does not establish strict tools.

Combined tools and structured output were tested separately by endpoint and tool choice:

| Endpoint and choice | vLLM | SGLang | Ollama |
| --- | --- | --- | --- |
| Chat, auto | Pass | Pass | Pass |
| Chat, none | Pass | Pass | Pass |
| Chat, forced named tool | Pass | HTTP 400 | HTTP 200, required tool call absent |
| Responses, auto | Pass | HTTP 400 | Pass |
| Responses, none | Pass | Pass | Pass |
| Responses, forced named tool | Pass | HTTP 400 | HTTP 200, required tool call absent |

These findings refine the earlier broad “tools plus structured output” limitation.
Definitions being present are distinct from forcing a tool call. The gateway preserves
backend responses; these are acceptance classifications, not added gateway validation.

## Implementation research

The pinned SGLang image explicitly rejects a forced tool constraint combined with a
response-format constraint on the tested parser path. Its `full_assistant_ebnf` exception
is model/parser-specific (the inspected path uses `Glm47MoeDetector`), not a universal flag
that can safely be enabled for Qwen. Using a mismatched parser to avoid validation would
not constitute support. Responses auto also rejected the tested combination.

Ollama's [v0.40.11 tool-function type](https://github.com/ollama/ollama/blob/v0.40.11/api/types.go)
does not carry a strict field. Its
[Responses adapter](https://github.com/ollama/ollama/blob/v0.40.11/openai/responses.go)
explicitly marks tool choice unsupported; the
[Chat request type](https://github.com/ollama/ollama/blob/v0.40.11/openai/openai.go)
lacks that field too. GitHub's release API reported v0.40.11 as latest during this run.
There is no verified newer release or launch flag fixing the observed tool guarantees.
A stronger model cannot replace missing runtime enforcement.

## Failures and recovery

A separate gateway container with a one-second generation deadline interrupted an Ollama
stream without a successful terminal event. A second test stopped Ollama after its first
stream chunk. Both clients observed an interrupted HTTP stream, both retail records ended
failed, and neither recorded token usage or a charge. Each retained its Builder Core job.
The deadline request closed in about 2.1 seconds including cleanup; runtime-stop closure
was about 0.8 seconds. These measurements do not establish an exact one-second socket-close
SLO. After model reload, the gateway recovered to HTTP 200 in about 13.7 seconds.

A vLLM overlap probe observed node capacity zero while streaming and a second request
received 503. Immediately after cancellation the catalog could still advertise the
runner while another request received 503. Discovery is a cached availability sample,
not a capacity reservation. Production concurrency, freshness and admission remain an
operational design concern; these tests must not be presented as a load test.

## Paid path

The user authorized the CloudSPE gateway identity and Xode orchestrator identity ending
`3c7f`, with no spending cap. The test reused the already running CloudSPE signer and
Clearinghouse instead of creating a second signer with the same payer. A private port
forward exposes those services only on local loopback. A separate paid Xode node uses
the corrected node image and Arbitrum One. Original keystores remain read-only; a local
owner-only password copy removes the file's trailing newline, as required by the node.

A dedicated $1 accounting grant/allocation was created for the test. The runner's price
was $0.001 per fixed request. This is an internal accounting allowance, not an on-chain
funding transfer. Four calls exercised unary success, Responses SSE success, backend
HTTP 400 and client disconnect. All four recorded a paid attempt and reconciled wholesale
expected-ticket-value cost of approximately $0.00099793614 each (about $0.00399174456 total).
The two successful generations received exactly one retail usage charge each. Failed and
disconnected requests retained unknown usage, no retail charge, and their observed
wholesale cost. Their retail billing state remains pending; no refund or fabricated token
count was introduced.

Winning-ticket redemption is separate from the Clearinghouse cost observation. Tickets
are persisted in the paid node's volume and cannot redeem until their parameter-expiry
block. A mined transaction is not finalized settlement. The final evidence records the
observed redemption state; do not infer chain finality from the successful HTTP calls.

## Remaining boundaries

The standard runner lock now pins the published, tested node candidate. This updates
artifact selection, not operator approval or production promotion. The matrices cover text and schema behavior on these exact
quantizations; images/audio, reasoning controls, long context, throughput, multi-node
failover and exhaustive JSON Schema support require separate acceptance. Feature-specific
admission remains absent: an endpoint approval does not certify strict tools or every
combination of request fields.
