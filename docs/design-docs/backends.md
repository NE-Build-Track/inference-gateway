# Add a backend

This is the existing translated/validated generation port. The opt-in
[native forwarding port](native-inference-routing.md) will preserve opaque protocol payloads
separately; see the [native contract](../product-specs/native-inference.md) for implementation and limits.

Implement `Backend.stream(Generate) -> AsyncGenerator[Event, None]` using core types.
Inject the instance into `create_app(backend=...)`. HTTP routes and protocol codecs
remain unchanged when a generation backend changes.

```python
from collections.abc import AsyncGenerator
from inference_gateway.app import create_app
from inference_gateway.core.models import (
    BlockStart, BlockEnd, Delta, Event, Finished, Generate,
)

class GreetingBackend:
    async def stream(self, request: Generate) -> AsyncGenerator[Event, None]:
        yield BlockStart(0)
        yield Delta(0, "Hello")
        yield BlockEnd(0)
        yield Finished("stop", input_tokens=0, output_tokens=1)

app = create_app(backend=GreetingBackend())
```

## Event contract

Start each block with its consecutive zero-based index. Emit deltas for that index,
then close the block. Client tool blocks may overlap; all other blocks are sequential.
A plain text block has no call. Extended `BlockStart.block` metadata represents cited text,
reasoning, atomic media or hosted results. Text/reasoning values start empty and arrive as
indexed deltas. Hosted tool starts use `ToolCall(hosted=True)` and require a matching
`HostedResult` after the call closes.
A tool block starts with `ToolCall(id, name, arguments="")`; its deltas must concatenate
to a JSON object. End with exactly one `Finished` and return. The service checks order,
block closure, argument JSON, and stream termination. Strict OpenAI tools additionally validate generated arguments against the supported schema.
It validates structured final text against the requested
schema before a successful finish. Output is bounded to 128 blocks and 8 MiB. See the
[extended contract](../product-specs/extended-generation.md).

`Finished` carries `stop`, `length`, or `tool_calls`, plus usage. Avoid blocking I/O in
an async generator. Raise `BackendError` for expected upstream failures. Exception details
remain private; clients receive generic protocol-shaped failures.

## Lifetimes and ownership

| Object/resource | Lifetime | Owner |
| --- | --- | --- |
| Backend instance | Application | Caller that creates it; borrowed by `create_app` |
| Upstream connection pool | Application | Backend creator; open/close in application lifespan |
| Generation iterator | Request | Service and HTTP boundary; explicitly closed |
| Request/response identity | Request | HTTP boundary |
| Fake fixtures | Backend | Fake instance; do not mutate supplied nested values |

An injected backend must support concurrent requests without sharing mutable request
state. Use `try/finally` around per-generation resources; cancellation may occur at
any await. Do not swallow cancellation or start an unowned background task. Constructors
should receive clients/settings and perform no I/O. If initialization opens several
resources, use an async exit stack so partial startup failure closes earlier resources.

The default fake owns no network resources. Livepeer mode uses `runtime.py` and application lifespan to own its engine.
A new backend that owns clients should follow that lifecycle. `create_app` deliberately does not close
borrowed objects. Core and adapter modules must not read environment variables or import
a DI container. Configuration loading belongs in the application root.

## Verify the integration

Add contract tests for text, tools, output limits, stream order, malformed output,
prestream failure, midstream failure, deadline expiry, and disconnect cleanup. Run SDK
round trips through both inbound adapters. Prove that live output reaches clients before
the upstream finishes. Tests using an in-memory HTTP transport alone cannot prove that.

For a new endpoint family, define its acceptance contract first. Add a separate port
when the capability differs from generation, wire it at the application root, and add
its protocol adapter and compatibility tests. Do not add unused placeholder routes.

The implemented [Livepeer adapter](livepeer.md) shows public-port integration, failure mapping, and cancellation.
