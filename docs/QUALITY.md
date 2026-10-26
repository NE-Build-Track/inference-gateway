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
ChatLPT price version. Wholesale observations resolved with job correlation. This tests
backend cost evidence, not ticket redemption settlement or the entire shared allocation.
The test uses only public HTTP; key and price provisioning uses the CLI.
