# Strict function tools and structured text

Accept optional per-tool strict:true on Responses and Chat Completions; omitted/false remain
non-strict for gateway compatibility. Validate supported schemas before dispatch, preserve strict
on both runner wire formats, and independently enforce generated arguments. Strict schemas require
closed objects and all properties required; support nullable types for optional values.

Allow tools together with structured final text. Validate tool-only turns against the tool schema,
not an absent text answer; validate any final text independently. Streaming deltas are provisional;
invalid calls must never receive argument/item completion or response success events. Consume the
bounded remainder to capture actual terminal usage, preserve failed accounting and close normally.

Test JSON/SSE, native/chat transport, invalid arguments, mixed strict/non-strict tools, structured
text with tools, usage/charges and cleanup. Run make check, build package/image, publish the fixed
v0.0.1 gateway image and record its digest. Live inference remains deferred.

## Verification

`make check` passed: 360 tests, Ruff, typing and documentation checks. `uv build` produced
the wheel and source distribution. The `strict-tools` Docker image passed an isolated,
network-disabled fake-backend smoke for strict tools combined with structured text in JSON
and SSE. All 34 packaged Python source hashes matched the tested working tree.

Tests cover native Responses and Chat Completions runners, SDK stream assembly, nullable
and nested schemas, mixed strict/non-strict tools, invalid arguments, final structured text,
cancellation during usage draining and exactly-once accounting. Live verification remains
deferred; the development gateway, signer and clearinghouse remain stopped.

Published `registry.livepeer.tools/ne-build-track/inference-gateway:v0.0.1` with digest
`sha256:4d045830cb7ec4c14843d93685a2656c371925e67b98b8e68eec3301fca7bdf9`.
