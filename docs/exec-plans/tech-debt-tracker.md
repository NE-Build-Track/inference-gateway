# Technical debt and deferred scope

Repository maintainers own these entries. None is represented as implemented behavior.

| Item | Trigger / next action |
| --- | --- |
| Live paid acceptance | Verify the intended existing payment stack, allowance, and approved test spend before running a paid smoke |
| OpenAPI request bodies | Expose request schemas with protocol-specific validation errors preserved |
| Production resource controls | Before shared hosting: request-body limits, per-tenant concurrency quotas, proxy TLS, tenant authentication |
| Observability | Define SLOs, then add metrics, tracing, and validation/failure counters |
| Full protocol semantics | Expand the compatibility matrix and fixture coverage as client requirements appear |
| Argument schema checking | Decide whether backend or gateway owns schema validation before claiming strict tool guarantees |
| Durable usage-report retry | Livepeer forwards real token counts; retry/reconcile a failed report from correlated warnings without rerunning inference |
| Additional endpoints | Specify the next capability; add a port and adapters where needed |
| Library extraction | Separate distributions when an external consumer needs independent releases |
| Documentation freshness | Add targeted semantic checks when drift occurs; current checks cover links and required files |
| Slow-client deadlines | Generation waits are timed; a stalled socket send relies on server/proxy behavior |
