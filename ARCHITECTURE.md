# Architecture

The gateway translates two external conversation protocols into a small generation
contract. It owns no conversation history. Clients submit history on every request.

```text
OpenAI clients ────┐
                  ├─ HTTP boundary → protocol translators → GenerationService → Backend
Anthropic clients ┘                                                          │
                                                          FakeBackend now ──┘
```

The generation context is the project's core capability. Protocol translation is
supporting code. HTTP serving is generic infrastructure. These are modules in one
application; there is no requirement for separate services or persistent aggregates.

| Module | Responsibility | Allowed dependencies |
| --- | --- | --- |
| `core/models.py` | Messages, tools, generation commands, output events | Standard library |
| `core/backend.py` | Replaceable backend port | Core models |
| `core/service.py` | Enforce event order; assemble nonstream responses | Core only |
| `adapters/schemas.py` | Supported external request shapes | Pydantic, core JSON type |
| `adapters/translate.py` | Convert requests and validate tool-result references | Schemas, core |
| `adapters/responses.py` | JSON and SSE wire encoders | Core, standard library |
| `testing.py` | Reusable deterministic fake | Core, standard library |
| `app.py` | Compose, authenticate, enforce deadlines, route and log | All layers |
| `__main__.py` | Process entry point and logging setup | Uvicorn |

Dependency tests enforce the core boundary and prevent adapters from selecting the
backend. Constructors do no I/O. The application uses explicit constructor calls;
a DI container would add a dependency without simplifying this graph.

One ordered event stream feeds both response modes. Streaming encodes events as they
arrive; nonstreaming accumulates them into a completion. Events are transient output
fragments, not persisted domain events. Request and response IDs provide correlation,
not durable deduplication. There is no retry, replay, cache, database, or message broker.

The inbound adapters protect the core from external vocabulary changes. This costs
translation code and contract tests, but allows either interface to evolve independently.
New endpoint families should get their own command, port where needed, and adapters;
do not force embeddings or stored responses through `Generate`.

See [decisions](docs/design-docs/decisions.md), [backend ownership](docs/design-docs/backends.md),
and the [compatibility contract](docs/product-specs/compatibility.md).
