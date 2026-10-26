# Livepeer extended capability gaps

Probe date: 2026-10-26. Route: `qwen3.8-27b`, application `blueclaw/chat/qwen3.8-27b`.
These are observations through Builder Core, not upstream OpenAI/Anthropic service tests.
Gateway/fake contracts are implemented and tested separately. The Livepeer adapter now supports
per-route structured output, reasoning on/off, stop sequences and parallel tools. They are enabled
on the local Qwen deployment after the acceptance work recorded below. Other features remain guarded.

Evidence: [successful baseline](../references/capability-probes/2026-10-26-baseline.json),
[baseline timeout](../references/capability-probes/2026-10-26-baseline-timeout.json), and
[14 feature probes](../references/capability-probes/2026-10-26-features.json). The feature run
reused the recent successful baseline after a separate baseline timed out. No failed operation
was retried. Reports contain job/operation references and structural signals, not response content.
The deployment container was removed by activity outside this task before its temporary reports
and probe databases could be exported. The linked records transcribe the sanitized stdout captured
by the tool; they are not exports of the original database. Engine version and provider accounting
evidence were not retained. Do not use these transcriptions as reusable baseline input. Future
container runs should stream the report to durable host storage during execution.

A total of 16 operations were dispatched: two baselines and fourteen feature requests.

| Capability | Observed result | Work needed before enabling |
| --- | --- | --- |
| Reasoning | Both public APIs passed live JSON/SSE acceptance | On/off control and unsigned history implemented; Anthropic signatures are empty. Effort levels, token budgets and signed history remain unsupported |
| Structured output | Nested schema passed live JSON/SSE acceptance through both APIs | Request forwarding and independent validation implemented; schema breadth remains the documented subset. Refusal/truncation covered offline |
| Stop sequences | Both public APIs passed live JSON/SSE acceptance, including Anthropic matched sequence | Forwarding, chunk-boundary filtering and evidence-based stop reporting implemented. Restricted to final-text requests without tools/thinking |
| Parallel tools | Passing live cases through both APIs and modes; one initial streaming assertion failed and a follow-up passed | Incremental interleaving, identity validation and fallback normalization implemented. Real model selection/arguments are not deterministic; see the acceptance qualification below |
| Top-p, top-k, seed, penalties | Accepted; effect inconclusive | Establish runner parameter support/configuration and controlled behavioral evidence; test forwarding; do not equate HTTP 200 with applied controls |
| Citations | **Unsupported Response:** requested annotations absent | Establish a source-grounding contract and citation-capable runner; map typed citations and offsets; natural-language links alone do not satisfy the contract |
| Hosted search | Inconclusive; no execution evidence | Supply a real hosted-tool executor and trusted execution/result evidence; map server calls/results; an accepted `web_search_options` field does not prove execution |
| Image input | Runner rejected request | Inspect runner capability and rejection evidence; select/configure a vision model; repeat with controlled valid images and semantic assertions |
| Audio input | Runner rejected request | Establish audio-capable model/input preprocessing; repeat valid audio probes and transcription/understanding assertions |
| Audio output | **Unsupported Response:** requested audio absent | Add audio generation or select an audio runner; validate bytes, format, usage and streaming |
| Image output | **Unsupported Response:** requested extension output absent | Define the runner-side generation interface; select an image-capable backend; bridge it to the gateway media contract |

“Unsupported Response” records a failed response contract in this probe. It is not proof that
all Livepeer routes lack the capability. A provider may ignore unknown request fields. The
citation and image-output probes are exploratory because this text runner has no established
native contract for those capabilities. Input rejection is not classified as a model limitation
without the runner's diagnostic evidence. The separate timeout is operational evidence only.

## Adapter acceptance and deployment

The [adapter matrix](../references/capability-probes/2026-10-26-adapter-acceptance.json) passed
15 of 16 cases. The Anthropic parallel-tool streaming case failed an assertion; the first driver
version did not retain which assertion failed. A separate
[targeted follow-up](../references/capability-probes/2026-10-26-adapter-parallel-recheck.json)
passed with two distinct calls, valid arguments and real usage. Preserve both outcomes: this is
passing coverage for each API/mode, not a claim that a model always chooses the requested tools.
The driver now retains safe assertion codes and structural signals on failures. Actual arbitrary
interleaving is verified with fragmented scripted transports; live two-call output alone does not
prove that the runner interleaved fragments.

A preceding [structured-output smoke](../references/capability-probes/2026-10-26-adapter-smoke.json)
also passed. The initial [connectivity run](../references/capability-probes/2026-10-26-adapter-connectivity.json)
returned 16 HTTP 503 responses; engine records show no payment sent. TLS verification failed for
the two US-central orchestrators with the existing pins. Validation was restricted to three
already configured orchestrators whose TLS checks passed; no certificates or verification rules
were weakened. These are operational failures, not capability failures.

The local gateway on port 8000 uses the accepted image and enables the four capabilities for
`qwen3.8-27b`. Its approved orchestrators are EU-central Open Pool, HKY EliteEncoder and AI1
EliteEncoder. The temporary port-8001 gateway was removed after acceptance; its engine database
remains in the existing data volume. The [image/source manifest](../references/capability-probes/2026-10-26-adapter-manifest.json)
identifies the image, source hashes and Builder Core revision. The
[sanitized engine evidence](../references/capability-probes/2026-10-26-adapter-jobs.json) covers
34 isolated operations: 16 unpaid connectivity failures and 18 paid inference attempts.

Two additional [production-mode smoke requests](../references/capability-probes/2026-10-26-production-smoke.json)
passed with registry authentication. Their reported usage matches the persisted ledger, each has
one retail charge and a correlated engine job. The temporary key was revoked and its test user
disabled. No existing credentials or prices were changed.

## Remaining work

Verify the other sampling controls, reasoning effort/budgets and additional schema keywords before
enabling them. Citations, hosted execution and media need runner capabilities or separate backend
integration. Investigate the two US-central TLS failures before restoring those origins; retain
certificate verification and the configured trust policy.

Capability declarations default to empty and apply per route. Do not enable the entire feature
set globally or infer support from HTTP 200. Continue recording operational failures, unverified
support and unsupported responses separately.


## Remaining-controls pass (2026-10-26)

[Raw report](../references/capability-probes/2026-10-26-remaining-features.json) and
[exported engine metadata](../references/capability-probes/2026-10-26-remaining-jobs.json) are direct
exports, not transcriptions. One successful baseline preceded thirteen feature attempts, each
limited to one attempt. Reported usage presence is a wire signal; this raw probe does not submit
usage to the engine or create retail charges. The engine metadata records payment dispatch and
attempt outcomes. Production route flags and prices were not changed.

| Remaining item | New evidence | Concrete Livepeer work and acceptance |
| --- | --- | --- |
| Sampling | All four probes accepted; exact forwarding now tested through SDKs | Inspect runner effective parameters/version; verify top-p/top-k/penalties and seeded repeatability under controlled configuration before enabling `sampling` |
| Reasoning effort | Low accepted with reasoning; high rejected | Configure supported model-specific effort levels; verify effective template settings and behavior, then declare only verified `reasoning_efforts` |
| Thinking budget | 32-token raw runner request accepted with reasoning | Verify enforced reasoning token count with runner tokenizer/telemetry; configure vLLM budget support before enabling `reasoning_budget`. API Anthropic budgets remain at least 1024 |
| Image input | Valid 128px blue PNG rejected | Configure a vision-capable chat runner; pass color test and SDK lifecycle/usage acceptance before enabling `media_input` with `input_media:["image"]` |
| Audio input | Valid one-second mono WAV rejected | Add an audio-capable chat runner or an explicitly accounted transcription workflow; validate spoken-content understanding before enabling audio |
| Citations | **Unsupported Response**, annotations absent | Define runner citation/source IDs and validated offsets; implement translation and provenance validation. Ordinary links in prose are insufficient |
| Hosted search | Accepted field, no execution evidence | Supply search executor, bounded invocation policy, correlated call/results and actual usage. A generated function call is not executed search |
| Audio output | **Unsupported Response**, audio absent | Add a chat audio contract or an explicit speech workflow, including bytes/format, stream limits, cancellation and per-job usage |
| Image output | **Unsupported Response**, media absent | Define image-generation composition and accounting, validate actual bytes, and preserve cleanup across both inference jobs |

Signer discovery listed one chat app (`blueclaw/chat/qwen3.8-27b`), plus separate Whisper,
Kokoro and image-generation applications. This establishes advertised supply, not chat API
compatibility or verified capacity on every approved origin. The gateway does not silently
substitute those endpoints for chat or charge a second operation without a defined workflow.
The current text deployment retains its four verified capabilities. Additional forwarding is
implemented but unenabled; hosted search, citation and media-output integration remains open.
