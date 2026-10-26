# Model-aware discovery and stateless Responses

## Acceptance

- Use JSON runner metadata `model_name` as identity and upstream model. Never derive it from app.
- Filter by model before paid dispatch through an injected Builder Core SelectionPolicy.
  Preserve app boundaries; choose an eligible configured app for duplicate model offerings.
- Authenticated OpenAI GET /v1/models exposes approved, available models with retail prices in
  registry mode, never performs inference, and excludes invalid metadata.
- POST /v1/responses supports stateless text/image input, function tools and full-history tool
  results, nonstream and typed SSE. Reject storage, previous-response references and unsupported
  fields explicitly. Preserve cancellation, deadlines, usage and paid-job correlation.
- Support explicitly configured native Responses runners as well as Chat Completions translation.
  Validate native terminal output/usage and stream lifecycle before reporting success.
- Verify SDK behavior, multi-orchestrator selection, failures and accounting; run make check,
  package build and Docker build. Do not deploy or overwrite the published v0.0.1 tag.

## Design

Model routes remain operator approval and capability declarations. Names match metadata exactly;
`upstream_model`, when supplied, must equal the name. Duplicate names may identify different apps.
A route's `upstream_api` selects `chat_completions` (default) or `responses`. These declarations
are explicit endpoint approval, not inferred runner capabilities. Selection wraps the engine's
existing ranker. Cross-app routing chooses capacity before creating one paid job; automatic
cross-app retries are not introduced.

## Verification and outcome

Implemented within this repository; the pinned Builder Core dependency is unchanged. The injected
policy raises NoCapacity when filtering yields no candidates because the pinned engine assumes a
nonempty ordered list. A real-engine test verifies the resulting job closes failed without any
transport attempt or payment. Gateway preflight normally rejects absent supply before job creation.

`make check` passed with 326 tests, strict typing, Ruff and documentation validation.
`uv build` produced the wheel and source distribution. Docker image
`inference-gateway/gateway:models-responses` built successfully with image ID
`sha256:fe7b9becb0ebd85e1e7f3df1f9bc9add2996f7c7b063c6ce08e8421dba06b886`.
All 34 Python source hashes match the working tree; the
[build manifest](../../references/models-responses-build.json) records provenance and the fact
that this includes uncommitted changes. An isolated, network-disabled container passed HTTP model
listing, Responses JSON/SSE and a function-call/result follow-up using the fake backend.

Live verification was explicitly deferred by the user. No paid acceptance requests were made in
this implementation. Read-only discovery inspection found the available Qwen chat advertisements
missing `model_name`; those need registration updates before this version can route them. No images
were pushed, v0.0.1 tags were not replaced, and running services were not changed. Live Gemma
acceptance, storage/previous-response references and per-runner endpoint capability metadata remain
listed limitations rather than shipped claims.
