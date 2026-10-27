# Technical debt and deferred scope

Repository maintainers own these entries. None is represented as implemented behavior.

| Item | Trigger / next action |
| --- | --- |
| Live operational coverage | The inference and multi-user accounting black-box suites pass; load, live disconnects, and provider-failure drills remain outside that matrix |
| OpenAPI request bodies | Expose request schemas with protocol-specific validation errors preserved |
| Production resource controls | Before shared hosting: request-body limits, per-tenant concurrency quotas, proxy TLS |
| Observability | Define SLOs, then add metrics, tracing, and validation/failure counters |
| Full protocol semantics | Expand the compatibility matrix and fixture coverage as client requirements appear |
| Schema compatibility | Strict OpenAI tools and structured text validate locally, including non-recursive local references. Recursive/external references and other unsupported JSON Schema keywords remain rejected; Anthropic strict tools remain outside the contract. |
| Client idempotency | Requests are distinct paid operations; add a replay/duplicate-request contract before enabling transparent retries |
| Reporting scale | Reports cap authorized requests at 10,000 per window; introduce indexed SQL aggregation/export when measured demand exceeds this |
| Accounting operations | Automate backups and reconciliation alerts; incomplete usage remains explicit and may require an operator adjustment |
| Additional endpoints | Specify the next capability; add a port and adapters where needed |
| Library extraction | Separate distributions when an external consumer needs independent releases |
| Documentation freshness | Add targeted semantic checks when drift occurs; current checks cover links and required files |
| Slow-client deadlines | Generation waits are timed; a stalled socket send relies on server/proxy behavior |
| Livepeer remaining capabilities | Four text capabilities have live acceptance; guarded sampling/effort/budget/media-input forwarding now has offline SDK coverage. Live probes still leave these unverified or rejected; citations, hosted execution and media output need runner integration; see [capability gaps](livepeer-capability-gaps.md) |
| Extended protocol breadth | Current structured output uses an explicit schema subset; hosted tools cover one web search; media uses documented extensions/fixtures. Expand only with acceptance cases in [the contract](../product-specs/extended-generation.md) |
| Live route availability and model variability | Two configured US-central origins failed TLS verification; excluded locally pending investigation. One parallel-call acceptance assertion failed before a successful follow-up; retain evidence and avoid deterministic tool-selection claims |

Models/Responses: live Gemma acceptance remains pending runner access with `metadata.model_name`.
Stored Responses, Anthropic model-list formatting and automatic cross-app retry are not
implemented. Native routes now use endpoint capability metadata with independent approval. See the [contract](../product-specs/models-and-responses.md).

Native forwarding is opt-in, with offline, bounded Qwen3.8 GPU and paid accounting evidence.
Dependency PR review, chain finality and production deployment admission remain. Approval automation/hot revocation,
shared-capacity multi-model layouts, compressed payload observation and stateful APIs need
separate work; see the [native plan](active/native-inference-forwarding.md).

Remote GPU acceptance found deployment-specific gaps; see the
[Qwen3.8 results](../../live-runner/tests/qwen38-followup-2026-10-27.md). The corrected node
is published and pinned. SGLang rejects forced tools combined with structured output and
the tested Responses auto combination; Ollama fails forced-tool choice and strict argument
constraints. Endpoint admission is not feature certification. Cached capacity can cause
temporary 503s around cancellation and discovery refresh (15 seconds by default);
load/concurrency acceptance must address this. Four paid calls reconciled expected-ticket
costs; winning-ticket chain finality remains separately observable in the retained paid node.
