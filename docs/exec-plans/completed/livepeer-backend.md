# Livepeer Builder Core backend

Status: completed 2026-10-25. Started 2026-10-25.

## Intent and accepted decisions

Replace the runtime fake with a selectable embedded Builder Core adapter while keeping
both public protocol interfaces and offline tests. Use one shared application allocation,
configurable model routing initially targeting `blueclaw/chat/qwen3.8-27b`, and a configured
application/actor identity. On disconnect, invoke the engine's disconnect handling and
preserve its recorded outcome. No gateway retries of paid jobs. Retain FakeBackend.

Use the payment-stack deployment configuration and pin Builder Core to revision
`629f1ab0ff84d1b350db7aea66c01989262d1325`. Secrets stay ignored and out of build contexts.
No live paid calls, provisioning, or signer launch are part of verification.

## Acceptance

- Model routing rejects unknown names before dispatch. Actor identity is server-controlled.
- Both APIs support streamed and nonstream text, tool calls and tool-result follow-ups.
- SSE parsing handles fragmented UTF-8, line boundaries, usage, terminal markers and errors.
- Multiple upstream tools map to sequential output blocks with bounded buffering.
- Client disconnects and timeout/error cleanup leave the engine's final outcome intact.
- Engine resources start/stop through application lifespan; invalid config fails startup.
- Request/job correlation and token reporting work without exposing secrets or prompts.
- SDK, engine-fake, real-engine/offline transport, socket and lifecycle tests pass.
- Locked package and Docker image build; Compose and documentation checks pass.

## Steps

1. Inspect pinned public APIs and stream lifecycle; establish tests and configuration contract.
2. Implement request encoding, SSE/event decoding, adapter and safe cleanup.
3. Implement engine composition, discovery filtering, readiness and runtime configuration.
4. Connect deployment settings and update operator/developer documentation.
5. Run checks, package/image builds and offline container smoke; record evidence and limits.

## Progress and decisions

- Inspected ActorApi, JobStreamApi, EngineSettings and shipped FakeEngine/make_engine helpers.
- The first tool can stream immediately. Later interleaved calls buffer until the first closes;
  a bounded buffer prevents unbounded accumulation while retaining sequential core blocks.
- Engine transport completion and valid model-protocol completion are distinct. A malformed
  runner response must fail the gateway even when the engine records successful byte delivery.
- The engine owns durable job/cost records. Gateway request IDs become operation references;
  this provides correlation, not cross-request idempotency or automatic result replay.

## Verification and remaining limits

- `make check`: 101 tests passed; Ruff, formatting, strict source mypy and documentation checks passed.
- `uv build`: wheel and source distribution built from the locked dependencies.
- Docker image built; offline smoke checked both protocols in JSON and SSE modes with
  FakeBackend and with real Builder Core plus a scripted transport.
- Base Compose and the payment-stack profile validated. The original external-network
  overlay has since been removed in favor of the standalone deployment.
- Copied private environment and credentials remain ignored; original source files are unchanged.
- SDK tests verify HTTP retry suppression. Connection failures still require callers to
  disable SDK retries explicitly when repeating a paid request is unacceptable.
- No live inference, funding, provisioning or payment-stack startup was performed.
  Live acceptance, usage-report recovery and production capacity limits remain in the debt tracker.
