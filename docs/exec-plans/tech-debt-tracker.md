# Technical debt and deferred scope

Repository maintainers own these entries. None is represented as implemented behavior.

| Item | Trigger / next action |
| --- | --- |
| Live operational coverage | The inference and multi-user accounting black-box suites pass; load, live disconnects, and provider-failure drills remain outside that matrix |
| OpenAPI request bodies | Expose request schemas with protocol-specific validation errors preserved |
| Production resource controls | Before shared hosting: request-body limits, per-tenant concurrency quotas, proxy TLS |
| Observability | Define SLOs, then add metrics, tracing, and validation/failure counters |
| Full protocol semantics | Expand the compatibility matrix and fixture coverage as client requirements appear |
| Argument schema checking | Decide whether backend or gateway owns schema validation before claiming strict tool guarantees |
| Client idempotency | Requests are distinct paid operations; add a replay/duplicate-request contract before enabling transparent retries |
| Reporting scale | Reports cap authorized requests at 10,000 per window; introduce indexed SQL aggregation/export when measured demand exceeds this |
| Accounting operations | Automate backups and reconciliation alerts; incomplete usage remains explicit and may require an operator adjustment |
| Additional endpoints | Specify the next capability; add a port and adapters where needed |
| Library extraction | Separate distributions when an external consumer needs independent releases |
| Documentation freshness | Add targeted semantic checks when drift occurs; current checks cover links and required files |
| Slow-client deadlines | Generation waits are timed; a stalled socket send relies on server/proxy behavior |
