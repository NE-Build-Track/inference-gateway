# Initial scaffold

Status: complete, 2026-10-25. This record captures the implementation and verification
performed during scaffolding.

## Acceptance

- OpenAI Chat Completions and Anthropic Messages routes share a replaceable backend.
- Text, incremental streaming, tool calls, and client tool-result follow-ups work.
- A deterministic fake runs locally without credentials or network inference.
- Core dependencies stay independent of protocol and framework code.
- Repository instructions, contracts, CI, and tests support future agent changes.

## Decisions and work

Built a single Python 3.12 application with FastAPI, a core backend protocol, request
translators, and separate stream codecs. The fake emits events incrementally. Nonstream
responses accumulate the same events. Constructor injection supplies backend instances;
application startup owns configuration. Added optional shared-key authentication,
generation deadlines, safe error envelopes, request correlation, and cleanup paths.

Used the requested harness article and design/writing skills to guide the repository
map, local knowledge, explicit boundaries, and proportionate design decisions. No
storage, broker, container framework, or real provider integration was needed.

## Verification

`make check` runs Ruff, strict source mypy, the test suite, and documentation checks.
SDK tests cover both protocols' text/tool round trips. Real-socket tests prove partial
output and disconnect cleanup. Failure tests cover startup errors, midstream errors,
and deadlines. `uv build` checks distribution packaging.

An SDK test exposed additional nullable fields in Anthropic tool-use serialization;
the request model now accepts those null fields. Its stream helper also wraps text in
a parsed type; the test compares protocol-visible content rather than Python class identity.

Remaining scope is recorded in the [debt tracker](../tech-debt-tracker.md).
