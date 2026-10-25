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
or schema-based argument generation. No live provider account was used for verification.
Request models define the supported schema; generated OpenAPI request-body coverage is
tracked as debt. See the [debt tracker](exec-plans/tech-debt-tracker.md).

Livepeer tests exercise both SDKs through the adapter, fragmented SSE, malformed responses,
interleaved tool calls, public error mapping, lifecycle failure cleanup, and real Builder Core
services using scripted transports. File-backed SQLite socket tests cover disconnects before
headers, during streaming, and during nonstream collection. They assert paid uncertainty,
closed transport, and released engine slots. Engine failover is checked before payment only.

The integration verification run passed 101 tests. Both offline image smoke scripts passed;
the Livepeer smoke uses the real engine with scripted transport and networking disabled.
