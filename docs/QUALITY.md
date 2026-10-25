# Quality status

Initial verification: 2026-10-25. Re-run `make check` after changes; CI runs the same
checks with the lockfile. These are behavior checks, not a claim of complete API parity.

| Area | Evidence | Remaining scope |
| --- | --- | --- |
| Text and tools | Official SDK request/response tests for both protocols | Broader provider feature matrix |
| Streaming | SDK accumulation and real-socket first-delta tests | Interleaved backend blocks, slow-client load tests |
| Failures | Before/after-start failures, deadline, cleanup tests | Upstream-specific errors and capacity policies |
| Isolation | Concurrent requests and dependency/import tests | Tenant policies and resource quotas |
| Maintainability | Ruff, strict source mypy, documentation link checks | Automated semantic documentation drift detection |
| Packaging | Locked environment and wheel/source build | Release automation and deployment artifacts |
| Observability | Correlated logs and response headers | Metrics, traces, operational SLOs |

The fake provides deterministic fixtures, not inference quality, accurate token counts,
or schema-based argument generation. No live provider account was used for verification.
Request models define the supported schema; generated OpenAPI request-body coverage is
tracked as debt. See the [debt tracker](exec-plans/tech-debt-tracker.md).
