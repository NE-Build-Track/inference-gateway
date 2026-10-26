# Architecture

The gateway translates two external conversation protocols into a small generation
contract. It owns no conversation history. Clients submit history on every request.

```text
OpenAI clients ────┐
                  ├─ HTTP boundary → protocol translators → GenerationService → Backend
Anthropic clients ┘                                                          │
                                                          FakeBackend or LivepeerBackend ──┘
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
| `adapters/reports.py` | Authorized provider and gateway report formats | Core, ledger adapter, FastAPI |
| `cli.py` | Local user/key/price/adjustment administration | Core, ledger adapter |
| `core/service.py` | Enforce event order; assemble nonstream responses | Core only |
| `adapters/schemas.py` | Supported external request shapes | Pydantic, core JSON type |
| `adapters/translate.py` | Convert requests and validate tool-result references | Schemas, core |
| `adapters/responses.py` | JSON and SSE wire encoders | Core, standard library |
| `testing.py` | Reusable deterministic fake | Core, standard library |
| `backends/` | Encode runner requests, decode SSE, call Builder Core, filter discovery | Core, configuration, public Livepeer API |
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
Builder Core owns its durable job/payment/cost records and eligible prepayment failover.
Gateway request IDs become engine operation references for correlation. Client disconnects
stop reading and preserve the engine's outcome; no application reply is saved.

The inbound adapters protect the core from external vocabulary changes. This costs
translation code and contract tests, but allows either interface to evolve independently.
New endpoint families should get their own command, port where needed, and adapters;
do not force embeddings or stored responses through `Generate`.

See [decisions](docs/design-docs/decisions.md), [backend ownership](docs/design-docs/backends.md),
and the [compatibility contract](docs/product-specs/compatibility.md).

See [the Livepeer design](docs/design-docs/livepeer.md) for paid stream lifecycle and bounded tool buffering.

See [accounting](docs/design-docs/accounting.md) for identity, price versions, reporting and recovery.
