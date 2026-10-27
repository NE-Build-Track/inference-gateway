# Non-recursive local schema references

Accept local JSON Pointer `$ref` references within the submitted schema, including `$defs`
and legacy `definitions`, for strict functions and structured text. Resolve escaped pointer
segments and chains without fetching external resources. Preserve the original wire schema.
Reject missing/non-schema targets, recursion, unsupported reference siblings and excessive
expansion before dispatch. Keep strict object requirements and validate generated values
against the referenced constraints. Non-strict tools retain their existing behavior.

Verify shared definitions, arrays, nullable branches, reference chains, root references,
invalid schemas and invalid generated arguments in JSON/SSE. Existing usage and accounting
regressions must continue to pass. Run `make check` and build the updated package/image.
Live verification remains deferred and the development stack remains stopped.

## Verification

`make check` passed: 391 tests, Ruff, typing and documentation checks. `uv build`
produced the wheel and source distribution. The image passed a network-disabled
fake-backend JSON/SSE smoke with strict tools and structured text using local references.
All 35 Python source hashes matched the tested working tree. Native/chat runner tests
verified referenced arguments, invalid output, original schema forwarding and accounting.

Published `registry.livepeer.tools/ne-build-track/inference-gateway:v0.0.1`:
`sha256:584d00b2ae412472452eed6dcb77a093ba6ee70b76c8524e13563aaa6dd0fb7a`.
Live acceptance remains deferred. Recursive/external references remain documented limitations.
