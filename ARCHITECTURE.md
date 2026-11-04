# Architecture

On validated routes, the gateway translates the OpenAI and Anthropic conversation protocols into
a small generation contract. On native routes, it forwards the original request and only observes
usage. System One decisions always use native routes. The gateway owns no conversation history;
clients submit history on every request.

```text
OpenAI clients ────┐
                   ├─ HTTP boundary ─┬─ protocol translators → GenerationService → Fake or LivepeerBackend
Anthropic clients ─┤                 │
                   │                 └─ native forwarding → LivepeerNativeBackend → Builder Core
TypeSafe clients ──┘
```

The generation context is the project's core capability. Protocol translation is
supporting code. HTTP serving is generic infrastructure. These are modules in one
application. Identity and retail accounting have durable state in a separate gateway database.

| Module | Responsibility | Allowed dependencies |
| --- | --- | --- |
| `core/models.py` | Messages, tools, generation commands, output events | Standard library |
| `core/backend.py` | Replaceable backend port | Core models |
| `core/accounting.py` | Principal, immutable prices, wholesale observation port | Core, standard library |
| `accounting.py` | Meter validated generation and reconcile accounting | Core, ledger adapter |
| `adapters/ledger.py` | SQLite identity, retail ledger and dated wholesale projections | Core, standard library |
| `adapters/ledger_writer.py` | Apply ledger writes on one thread, in order | Standard library |
| `adapters/reports.py` | Authorized provider and gateway report formats | Core, ledger adapter, FastAPI |
| `cli.py` | Local user/key/price/adjustment administration | Core, ledger adapter |
| `core/service.py` | Enforce event order; assemble nonstream responses | Core only |
| `adapters/schemas.py` | Supported external request shapes | Pydantic, core JSON type |
| `adapters/translate.py` | Convert requests and validate tool-result references | Schemas, core |
| `adapters/responses.py` | JSON and SSE wire encoders | Core, standard library |
| `adapters/model_catalog.py` | Per-SDK model visibility and list formats | Standard library |
| `testing.py` | Reusable deterministic fake | Core, standard library |
| `core/native.py` | Opaque native transport port | Core models |
| `native.py` | Native forwarding lifecycle and usage observation | Core, native usage adapter |
| `adapters/native_request.py`, `native_usage.py`, `native_http.py` | Inspect the routing envelope, read usage, deliver over ASGI | Core, runner contract |
| `adapters/runner_contract.py` | Endpoint profiles and runner metadata | Pydantic, standard library |
| `backends/` | Encode runner requests, decode SSE, call Builder Core, filter discovery | Core, configuration, runner contract, public Livepeer API |
| `config.py` | Frozen gateway and routing settings | Standard library, Pydantic |
| `runtime.py` | Load environment, construct/start/stop the engine | Configuration, backends, public Livepeer API |
| `app.py` | Own lifespan, authenticate, enforce deadlines, route and log | All layers |
| `__main__.py` | Process entry point and logging setup | Uvicorn |

Dependency tests enforce the core boundary and prevent adapters from selecting the
backend. The composition root opens the ledger and owns backend/worker lifetimes.
Domain constructors do no I/O. The application uses explicit constructor calls;
a DI container would add a dependency without simplifying this graph.

One ordered event stream feeds both response modes. Streaming encodes events as they
arrive; nonstreaming accumulates them into a completion. Events are transient output
fragments, not persisted domain events. Request and response IDs provide correlation,
not durable deduplication. Registry mode persists request attribution, measured usage and retail charges. Its
reconciliation worker retries accounting reads/writes, never generation. There is no
response replay cache or message broker. In Livepeer mode,
Builder Core owns its durable job/payment/cost records and failover, including paid failover to another orchestrator (its D52, capped by `LPB_MAX_PAID_ATTEMPTS`).
Gateway request IDs become engine operation references for correlation. Client disconnects
stop reading and preserve the engine's outcome; no application reply is saved.

The inbound adapters protect the core from external vocabulary changes. This costs
translation code and contract tests, but allows either interface to evolve independently.
New endpoint families should get their own command, port where needed, and adapters;
do not force embeddings or stored responses through `Generate`. System One decisions follow this
rule: they use native forwarding and never touch `Generate`.

See [decisions](docs/design-docs/decisions.md), [backend ownership](docs/design-docs/backends.md),
and the [compatibility contract](docs/product-specs/compatibility.md).

See [the Livepeer design](docs/design-docs/livepeer.md) for paid stream lifecycle and bounded tool buffering.

See [accounting](docs/design-docs/accounting.md) for identity, price versions, reporting and recovery.
