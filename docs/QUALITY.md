# Quality status

Verification: 2026-10-25. Re-run `make check` after changes; CI runs the same
checks with the lockfile. These are behavior checks, not a claim of complete API parity.

| Area | Evidence | Remaining scope |
| --- | --- | --- |
| Text and tools | Official SDK request/response tests for both protocols | Broader provider feature matrix |
| Streaming | SDK accumulation and real-socket first-delta tests | Slow-client load tests; later interleaved tool blocks are bounded and buffered |
| Failures | Before/after-start failures, deadline, cleanup tests | Live network behavior and tenant capacity policies |
| Isolation | Concurrent requests and dependency/import tests | Tenant policies and resource quotas |
| Maintainability | Ruff, strict source mypy, documentation link checks | Automated semantic documentation drift detection |
| Packaging | Locked environment, wheel/source build, Docker image and offline container smoke | Release automation and deployment artifacts |
| Observability | Correlated logs and response headers | Metrics, traces, operational SLOs |

The fake provides deterministic fixtures, not inference quality, accurate token counts,
or schema-based argument generation. Live verification used the configured Livepeer shared application allocation.
Request models define the supported schema; generated OpenAPI request-body coverage is
tracked as debt. See the [debt tracker](exec-plans/tech-debt-tracker.md).

Livepeer tests exercise both SDKs through the adapter, fragmented SSE, malformed responses,
interleaved tool calls, public error mapping, lifecycle failure cleanup, and real Builder Core
services using scripted transports. File-backed SQLite socket tests cover disconnects before
headers, during streaming, and during nonstream collection. They assert paid uncertainty,
closed transport, and released engine slots. Engine failover is checked before payment only.

The integration verification run passed 104 tests. Both offline image smoke scripts passed;
the Livepeer smoke uses the real engine with scripted transport and networking disabled.

Live black-box acceptance on 2026-10-25 passed all 12 cases against the deployed gateway
and `qwen3.8-27b`: text, forced tool calls, and tool-result follow-ups in JSON and streaming
mode through both official SDKs. This verifies the OpenAI and Anthropic API formats over
Livepeer; it does not test inference hosted by OpenAI or Anthropic. Run `make test-live`
to repeat the matrix. These are paid, opt-in checks with SDK retries disabled.

The first live run exposed a runner returning `stop` for complete forced tool calls. The
adapter now normalizes that outcome after validating the call; regression tests retain
rejection of truncated or malformed calls. The final live run passed after rebuilding the
image. Availability and model behavior can change; this is a dated acceptance result.

Multi-user accounting verification: 2026-10-26. The expanded offline suite passed 129 tests,
including CLI-driven HTTP black-box checks against a separate process, user/key isolation,
rotation and expiry, immutable price versions, exact cached-input pricing, currency grouping,
append-only adjustments, concurrent duplicate writes, migration and restart recovery.
Strict source type checks, formatting, lint and documentation checks passed. Package and
container builds succeeded.

A live HTTP-only run made 14 requests through the registry-enabled gateway: both protocols'
text, tool calls and follow-ups in JSON and SSE, plus a second key and a second user. Response
usage matched request records and provider reports; retail arithmetic matched the configured
immutable price version. Wholesale observations resolved with job correlation. This tests
backend cost evidence, not ticket redemption settlement or the entire shared allocation.
The test uses only public HTTP; key and price provisioning uses the CLI.

Public deployment verification: 2026-10-26. The renamed `inference_gateway` package
passed all 132 offline tests, strict typing, lint, formatting, documentation checks,
and wheel/source builds. The rebuilt container passed readiness and both protocols
in JSON and SSE modes. Gateway and orchestrator Compose configurations validate;
renderer tests cover offchain and onchain configuration. Local vLLM GPU inference
and the new onchain orchestrator have not been exercised on this machine.

The standalone gateway cutover preserved all 14 live acceptance request records,
including retail USD `0.001459000` and wholesale expected-value USD
`0.001564182350730314`. Authenticated HTTP detail/report reads and rejection of
unauthenticated reporting passed after the cutover. Existing payment identities and
immutable historical price labels were retained. The old volumes remain backups.


Extended generation verification: 2026-10-26. `make check` passes with 197 offline tests,
strict typing, lint/formatting and documentation checks. The added contracts cover reasoning,
sampling/stop controls, the documented structured-output schema subset, source citations,
simulated hosted search, interleaved client tools, and image/audio fixtures. Both SDKs consume
JSON and SSE output; tests also cover history, invalid input/output, audio-only responses,
unsupported-feature rejection before Livepeer dispatch, and interleaved generator cleanup.
Probe tests distinguish observed output from inconclusive semantic support and verify cleanup.

Sixteen paid Builder Core probe operations targeted the configured Qwen route: two baselines
(one succeeded, one timed out) and fourteen feature cases. Reasoning, structured JSON, stop
behavior and multiple call indices were observed. Citation annotations and image/audio output
were absent; image/audio input was rejected. Accepted sampling settings and hosted execution
remain inconclusive. See the [gap register](exec-plans/livepeer-capability-gaps.md) for evidence,
limitations and work needed in the Livepeer adapter. A container replacement prevented exporting
temporary probe databases; sanitized stdout and job IDs were retained instead. No production
feature was enabled automatically, and the new gateway code was not deployed by this task.


Livepeer adapter verification: 2026-10-26. `make check` passes with 252 offline tests. The added
checks exercise per-route guards, actual runner request fields, reasoning/text/tool order,
independent interleaving, stop markers across chunk boundaries, structured-output failures and
truncation, both SDKs, and real-engine cancellation/deadlines for all four features. The live
acceptance driver is itself exercised offline with both SDKs and modes.

The live matrix passed 15/16 cases; a separate Anthropic parallel-stream follow-up passed. The
initial assertion's specific cause was not retained, so both results remain in the
[evidence register](exec-plans/livepeer-capability-gaps.md#adapter-acceptance-and-deployment).
This establishes successful API/mode coverage with a documented variability caveat, not
universal model behavior. Initial unpaid TLS connectivity failures were resolved for validation
by restricting selection to existing trusted origins, without bypassing certificate verification.

The accepted image is deployed locally on port 8000 with registry accounting and the four route
capabilities enabled. Two production-mode structured-output smoke requests passed; usage and
single charges match the ledger. Temporary test credentials are revoked/disabled and the isolated
validation server is removed. Engine evidence, request IDs and source/image hashes are retained.
Effort/budget controls, general sampling, citations, hosted execution and media remain unenabled.


The remaining-controls pass added guarded sampling forwarding, per-route reasoning effort levels,
vLLM thinking budgets and explicitly typed image/audio input. SDK JSON/SSE tests verify delivery
through both public APIs, including zero-valued controls and route isolation. These paths remain
disabled in production. The [new raw probe run](references/capability-probes/2026-10-26-remaining-features.json)
and [engine evidence](references/capability-probes/2026-10-26-remaining-jobs.json) cover one baseline
and thirteen feature attempts. They establish transport observations, not stochastic control,
budget enforcement or hosted execution. Missing native citations/audio/images are recorded as
Unsupported Response. Valid media inputs and high effort were rejected by the runner.


Models and stateless Responses now have offline SDK/engine coverage described in the
[contract](product-specs/models-and-responses.md). Live Gemma acceptance is pending its route and
valid `model_name` metadata. Existing Qwen probe results predate strict metadata routing; they do
not certify the new routing or native Responses transport.

The completed Models/Responses implementation passed 326 tests and the full repository checks.
The candidate Docker image passed isolated fake-backend HTTP smoke tests for Models, Responses
JSON/SSE and a function-result follow-up. Its 34 Python files match the working tree; see the
[build manifest](references/models-responses-build.json). Live verification is deferred at the
user's request. No new image was published and no running service was changed.

Native boundary slice: `make check` passes 446 tests, lint, formatting, typing and docs.
Tests cover raw bytes, expiring admission, real-engine selection, fragmented usage in all
three protocols, cached tokens, errors, cancellation, deadline and pre-body cleanup. These
are offline components; they do not certify the paid raw transport or enable native HTTP
routes. See the [integration boundary](design-docs/native-transport-integration.md).

Native HTTP integration now passes `make check` with 465 tests, including original-body
forwarding for all three endpoints, registry accounting/job correlation, wholesale usage
reporting, HTTP errors, failed/incomplete Responses, expiry, auth, deadline and ASGI
disconnect cleanup. Subsequent GPU/paid evidence is recorded below. Builder Core
PR #55 has since merged as `2134e29`, and SDK PR #6 is closed, superseded by it.

The [27 October GPU run](../live-runner/tests/gpu-acceptance-2026-10-27.md) exercises real
vLLM, SGLang and Ollama through an offchain local node and the native gateway. It found
and fixed optional-null usage details, final Messages input/cache usage updates, and
missing node discovery identity. Runtime-specific strict-tool/combined-output limitations
remain documented. The [Qwen3.8 follow-up](../live-runner/tests/qwen38-followup-2026-10-27.md)
records 180 protocol probes, failure/recovery checks and four paid requests with observed
wholesale costs. Corrected images are published as NE candidates and the node lock is
updated. Chain finality, production load and feature-specific admission remain pending.
Gateway checks pass 472 tests after the usage regressions.

The [vLLM release follow-up](../live-runner/tests/vllm-release-2026-10-27.md) adds three
capacity/cancellation cycles, sequential requests, runtime restart and a fresh paid
gateway. Node race tests pass, including signer/proxy discovery identity. Production
promotion remains gated by review, chain finality and full deployment acceptance.

[Native vision acceptance](../live-runner/tests/vision-acceptance-2026-10-27.md) passes
24 image generation checks plus six malformed-image checks, including paid usage and
retail accounting. `make check` passes 481 tests. These are bounded inline-PNG fixtures,
not certification of every image format or other media.

The [repository compatibility review](references/repository-compatibility-2026-10-27.md)
passes 483 tests on current pins and an isolated latest-M3-plus-native overlay. Plain M3
is not a native-compatible replacement. Discovery Compose/startup wiring is corrected,
with a real local discovery-process regression. No live paid/GPU services were started.

[Metadata identity acceptance](../live-runner/tests/metadata-identity-2026-10-27.md) removes
node PR #1 from the dependency chain. All 500 tests and 36 real unpatched-node transport
checks pass; live GPU/payment acceptance of the new combined deployment remains separate.

The [28 October M3 review](references/repository-compatibility-2026-10-28.md) found that
Core `efb9c03` needed a new native port onto its engine-owned paid runner call: an isolated
full gateway run passed 473 tests and failed 27 native tests. The startup guard rejects that
installation before engine construction. Core PR #55 made the port and merged as `2134e29`.
On 29 October the gateway moved to the later `releases/m3` commit `d3df806`, which includes it
and brings the upstream SDK `2017a80`, and moved Node and Batteries builds to `livepeer/` `main`.
Paid failover stays on (`LPB_MAX_PAID_ATTEMPTS=2`). `make check` passes 532 tests. Paid
native vLLM acceptance passed on the rebuilt images with exact wholesale reconciliation; see the
[repin evidence](../live-runner/tests/m3-upstream-2026-10-29.md). That topology had one
orchestrator, so paid failover could not occur and is not yet exercised live. The repository's
live suites still assume validated-route tool semantics that native vLLM does not meet.
