# Technical debt and deferred scope

Repository maintainers own these entries. None is represented as implemented behavior.

| Item | Trigger / next action |
| --- | --- |
| Real backend integration | Choose provider and credentials; add owned lifespan, backend contract tests, and capability handling |
| OpenAPI request bodies | Expose request schemas with protocol-specific validation errors preserved |
| Production resource controls | Before shared hosting: body/output limits, concurrency quotas, proxy TLS, tenant authentication |
| Observability | Define SLOs, then add metrics, tracing, and validation/failure counters |
| Full protocol semantics | Expand the compatibility matrix and fixture coverage as client requirements appear |
| Argument schema checking | Decide whether backend or gateway owns schema validation before claiming strict tool guarantees |
| Usage accuracy | Use real backend token counts; fake character counts are fixtures only |
| Additional endpoints | Specify the next capability; add a port and adapters where needed |
| Library extraction | Separate distributions when an external consumer needs independent releases |
| Documentation freshness | Add targeted semantic checks when drift occurs; current checks cover links and required files |
| Slow-client deadlines | Generation waits are timed; a stalled socket send relies on server/proxy behavior |
