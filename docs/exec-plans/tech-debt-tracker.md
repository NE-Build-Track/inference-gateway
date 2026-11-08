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
| System One acceptance | [Verified offchain on four backends](../../live-runner/tests/clef-backends-2026-11-02.md). No decision has been paid for. Run the paid cases in [the plan](active/system-one-route.md) on a real Clef runner before describing the route as accepted |
| System One audio and video | The gateway refuses `audio`, `videos` and `files`, because [some backends ignore them](../../live-runner/tests/clef-backends-2026-11-02.md#audio-and-video) and return a normal answer. Add a per-route setting, as `media_input` does for validated routes, when a deployment proves its model uses them |
| Decision state limits | SGLang cuts long `state` without warning. Other backends are unmeasured. Measure them before relying on long state |
| Native stream outcomes | A native SSE request that was delivered and charged can record the outcome `disconnected`. Charges are correct; the outcome misleads reports. Find where the stream ends early and record success |
| SGLang local media paths | SGLang's image loader opens `file://` and `/path` values, and `--allowed-media-domains` covers only HTTP(S). System One admits only inline images, but native chat bodies are not inspected, so an SGLang vision route may have the same exposure; this path has not been traced end to end. Test and restrict it before admitting one |
| Per-call wholesale versus per-token retail | Decisions are short, so a flat wholesale price can exceed retail at ordinary input rates. Price from real token counts; add a per-call fee only if rates cannot cover it |
| Discovery process test timing | `test_discovery_process_preserves_native_runner_identity` failed once in a full local run and passed when repeated; the cause is unknown. It allows about 5 seconds for a subprocess to start. Widen the window if it recurs |
| Reporting scale | Request lists page in SQL without a cap. A bucket report still totals its page's rows in Python and fails above 10,000 requests per page; aggregate in SQL if pages grow past that |
| Ledger write throughput | One writer thread commits each write separately: about 12 ms per metered request on NVMe and 40 ms on a slow SSD, so metered requests top out near 80 or 25 per second. Batch commits if load approaches that |
| Core and capability App IDs | Builder Core keys per-app concurrency limits, its offerings catalog and job records on App ID, and under runner contract v1 every chat model shares `chat`. Key them on capability and model in Core, or give the gateway its own per-model limit, before relying on per-model caps or Core's per-app reports |
| Failover on 429 | The standard's entries map a runner's 429 to `busy`, safe to send elsewhere, but Core fails over only on 503, 404, 409 and refused payment. Raise it with Core; until then a busy runner fails the request |
| Readiness installs at start | Each readiness service installs `runner-contract` from GitHub and PyPI when it starts, so a runner can't turn ready while either is unreachable, and the build backend (hatchling) isn't pinned. Bake the tool into a digest-pinned image if starts fail or the build drifts. For Ollama it no longer checks the loaded model's digest |
| Shared capacity groups | A deployment serving chat and decisions registers twice with one `capacity_group`, but neither Core nor the gateway enforces groups, so the two can together exceed the runtime's slots; the runtime queues the excess. Enforce `capacity_group` in selection when such deployments carry traffic |
| Moved endpoint paths | Runners whose envelope overrides an entry's path or method aren't selected, because Core sends one path per job. Support per-runner paths in Core before admitting such runners |
| Anthropic SSE event types | The `anthropic.messages.v2` entry tells stream events apart by SSE event type, so a stream without `event:` lines now fails. The repository has no evidence on whether each backend sends them. Check vLLM, SGLang and Ollama with `runner-contract conform` before admitting their Messages endpoints |
| Accounting operations | Automate backups and reconciliation alerts; incomplete usage remains explicit and may require an operator adjustment |
| Additional endpoints | Specify the next capability; add a port and adapters where needed |
| Library extraction | Separate distributions when an external consumer needs independent releases |
| Documentation freshness | Add targeted semantic checks when drift occurs; current checks cover links and required files |
| Slow-client deadlines | Generation waits are timed; a stalled socket send relies on server/proxy behavior |
| Livepeer remaining capabilities | Four text capabilities have live acceptance; guarded sampling/effort/budget/media-input forwarding now has offline SDK coverage. Live probes still leave these unverified or rejected; citations, hosted execution and media output need runner integration; see [capability gaps](livepeer-capability-gaps.md) |
| Extended protocol breadth | Current structured output uses an explicit schema subset; hosted tools cover one web search; media uses documented extensions/fixtures. Expand only with acceptance cases in [the contract](../product-specs/extended-generation.md) |
| Live route availability and model variability | Two configured US-central origins failed TLS verification; excluded locally pending investigation. One parallel-call acceptance assertion failed before a successful follow-up; retain evidence and avoid deterministic tool-selection claims |

Models/Responses: live Gemma acceptance remains pending runner access with `metadata.model_name`.
Stored Responses, Anthropic model-list pagination and automatic cross-app retry are not
implemented. Native routes now use endpoint capability metadata with independent approval. See the [contract](../product-specs/models-and-responses.md).

Native forwarding is opt-in, with offline, bounded Qwen3.8 GPU and paid accounting evidence.
Dependency PR review, chain finality and production deployment admission remain. Approval automation/hot revocation,
shared-capacity multi-model layouts, compressed payload observation and stateful APIs need
separate work; see the [native plan](active/native-inference-forwarding.md).

Remote GPU acceptance found deployment-specific gaps; see the
[Qwen3.8 results](../../live-runner/tests/qwen38-followup-2026-10-27.md). The node-patch dependency is removed: runner identity now comes from validated metadata
when the top-level discovery field is absent; the lock pins an unpatched node. SGLang rejects forced tools combined with structured output and
the tested Responses auto combination; Ollama fails forced-tool choice and strict argument
constraints. Endpoint admission is not feature certification. Cached capacity can cause
temporary 503s around cancellation and discovery refresh (15 seconds by default);
load/concurrency acceptance must address this. Four paid calls reconciled expected-ticket
costs; winning-ticket chain finality remains separately observable in the retained paid node.

Builder Core M3 upgrade: done on 29 October (Core `d3df806`, which includes the PR #55 merge
`2134e29`; moved to the M3 merge `1514212` on 2 November; upstream SDK; `livepeer/` Node and Batteries); see the
[repin evidence](../../live-runner/tests/m3-upstream-2026-10-29.md). Paid failover is kept: Core
may fail a paid 503, 404 or 409 over to another orchestrator, at most `LPB_MAX_PAID_ATTEMPTS` (2)
paid attempts; set it to `1` to turn paid failover off. Live paid failover between two
orchestrators passed in the [release candidate check](../../live-runner/tests/release-candidate-2026-10-29.md).
The repin run left these open items:

- Capacity cost: after a cancelled stream, the next request can be paid and then refused
  with 503 while discovery still advertises a full slot. Each occurrence wastes one ticket.
  Fix through capacity freshness or reservation.
- Payment-layer statuses: a node payment-challenge failure reaches native clients as 502,
  and a rejected payment as 400, indistinguishable from runtime errors. Classify node-originated
  failures before forwarding, without altering genuine backend bodies.
- Live suites: `test-live-provider.py` and `test-accounting-http.py` require
  `finish_reason: "tool_calls"` for a forced named tool and read Anthropic `thinking` blocks
  as tool calls. Native vLLM returns `stop` and Qwen3 emits thinking blocks. Decide per route
  which semantics each suite asserts.
