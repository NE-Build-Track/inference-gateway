# System One route and per-SDK model lists

Decision models such as Cloudflare Clef answer typed questions with probabilities instead of
generating text. SGLang serves them at `POST /v1/systemone`, the shape of TypeSafe's System One
API. Clef refuses chat and generation requests, so no existing gateway route can reach it.

Adding a third API family also breaks `GET /v1/models`. Today every caller gets the same
OpenAI-shaped list of every eligible model, so a Clef model would appear to chat clients and a
chat model would appear to System One clients. This plan makes each SDK see only the models it
can use, in the format it parses.

## Acceptance

### `POST /v1/systemone`

- A native route forwards the original bytes, then records `usage.input_tokens` and
  `usage.output_tokens` once. Zero output tokens is valid; retail charges input only.
- Authentication, permissions, price snapshot, approval and deadlines match other native routes.
- These fail with 400 before dispatch: `stream: true`, because SGLang would ignore it and answer
  with JSON the gateway would read as a broken stream; `images`, because SGLang opens local paths
  and fetches any URL given there; `videos`, `assets` and `media_kwargs`, because SGLang drops
  them and would decide without them; compressed bodies.
- A model without a native route gets 400. A chat-shaped body posted here never runs as chat.
- A backend 400 or 422 keeps its status and body, carries `x-should-retry: false` and costs the
  customer nothing. A 200 without valid usage is a 502 with no charge.
- The route works through the real `typesafe-sdk` client with retries and compression off.

### `GET /v1/models`

| Caller | Detected by | Lists native models that serve | Format |
| --- | --- | --- | --- |
| Anthropic | `anthropic-version` header | `messages` | Anthropic page |
| TypeSafe | `X-TypeSafe-SDK` header | `systemone` | `{"models":[...]}` |
| Anything else | Default | `chat_completions` and `responses` | OpenAI list |

- A native model counts as serving an endpoint only if a runner advertises it and an operator
  approved it. Metadata alone cannot widen a list.
- Validated routes appear for OpenAI and Anthropic and never for System One, because the gateway
  translates them and nothing translates decisions.
- A runner whose contract metadata does not declare a validated route's upstream endpoint is
  ignored for that route, in listing and in paid selection, so a Clef route that forgets
  `mode: native` serves nobody.
- The Anthropic view accepts `x-api-key` or Bearer and uses Anthropic's error envelope.
- `limit`, `after_id` and `before_id` return 400; the view always returns one complete page.
- Retail-price and permission filters apply to every view.
- Readiness keeps its meaning: at least one model can serve at least one endpoint.

### Wrong endpoint

Calling an endpoint with no approval for that model returns 400 before payment instead of 503, so
clients do not retry a request that can never succeed. An approved model with no capacity, or an
expired approval, stays 503.

## Decisions

- **Forward; do not translate.** Native forwarding already separates the transport from the
  protocol. A dedicated `/v1/systemone` handler shares its lifecycle.
- **Detect the caller from headers.** All three SDKs call the same path. `anthropic-version` is
  part of Anthropic's protocol. `X-TypeSafe-SDK` is an SDK detail: if it is renamed, that SDK gets
  the OpenAI format and fails loudly, so it never shows a wrong model. A header cannot widen
  access, because it only selects a view of what the key may already see.
- **OpenAI requires both endpoints.** One SDK covers chat and Responses, and a list request does
  not say which the caller will use. Chat-only native models leave the OpenAI list but remain
  callable by name. The supported vLLM manifest advertises all three endpoints, so it stays listed.
- **Display fields live in route configuration.** Runners self-report metadata, and four endpoints
  already use 920 of its 1,024 bytes.
- **Text only at first.** See the acceptance list. Inline `data:` images can follow once they have
  acceptance evidence.

## Work

- [x] `systemone` endpoint profile, approval value and published contract files.
- [x] Envelope rules and usage parsing.
- [x] `/v1/systemone` handler and the wrong-endpoint 400.
- [x] Per-family model listing, formats and route display fields.
- [x] SDK contract tests with `typesafe-sdk` as a dev dependency.
- [x] Compatibility, native inference, runner contract and operations documents.
- [ ] Paid acceptance on a real Clef runner.

## Rollout

Changing `runner_contract.py` and `profiles.json` changes the deployment revision of any bundle
rendered afterwards, so each re-render needs new approvals. Runners rendered earlier keep working.
Deploy the gateway before any runner advertises `systemone`: older gateways reject metadata with
an unknown profile.

## Verification

`make check`: 589 passed, plus lint, formatting, strict typing and documentation checks. Live
acceptance needs one paid request per case on a Clef runner: decision round
trip, exact input-only retail charge, wholesale reconciliation, backend 422, each list view and
concurrent capacity. Nothing here has run against a live Clef server yet.

## Unresolved

- Whether the locked SGLang 0.5.22 image and its 2 GB `shm_size` serve Clef. The model card uses
  32 GB and `--ipc=host`.
- Concurrent capacity; the model card reports latency only.
- Retail and wholesale rates, which need real token counts per request.
- `live-runner/tests/probe_vision.py` waits for its model in the OpenAI list, so it now stops
  before any payment on a runner that does not advertise both chat and Responses.
