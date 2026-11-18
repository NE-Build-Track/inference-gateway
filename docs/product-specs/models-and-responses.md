# Model discovery and stateless Responses

This contract adds OpenAI-compatible `GET /v1/models` and `POST /v1/responses`.
It is a documented subset, not the entire OpenAI Responses service. Existing Chat Completions
and Anthropic Messages remain available.

## Model identity and routing

A runner names its model in its [runner contract v1](runner-contract.md) envelope:

```json
{"runner_contract":1,"deployment_revision":"sha256:…",
 "model":{"name":"gemma4:26b","source":"ollama:gemma4:26b","revision":"sha256:…"},
 "endpoints":{"responses":{"profile":"openai.responses.stateless.v2"}}}
```

`model.name` is the public model name and the upstream request's `model`. An App ID names a
capability, such as `chat`, never a model. A runner without a valid envelope is ignored. There is
no fallback to an App ID, a source or a default model.

Operators list model routes in `GATEWAY_MODEL_ROUTES`, each a model name and a capability (`chat`
by default). Duplicate pairs are rejected. An injected Builder Core `SelectionPolicy` filters
candidates by capability, model name, the endpoint's profile and positive (or unknown) capacity,
then delegates ordering to the existing policy. This also filters every retry candidate. Paid
attempts retain the engine's existing retry restrictions.

On validated routes, feature support remains an explicit operator declaration per route. A runner
must declare the route's upstream profile (`openai.chat.v2`, or `openai.responses.stateless.v2`
with `upstream_api: responses`), but that declaration doesn't advertise tools or vision, and every
matching runner must satisfy the route's declared contract. Native routes need an approval for each
runner and endpoint instead; see the [native contract](native-inference.md).

## Model lists

Every SDK calls `GET /v1/models`, and each should see only the models it can use. The gateway reads
the request headers to choose which models to list and which format to return:

| Caller | Detected by | Native models must serve | Format |
| --- | --- | --- | --- |
| Anthropic SDK | `anthropic-version` | `messages` | `{"data":[{type,id,display_name,created_at}],"has_more":false,first_id,last_id}` |
| TypeSafe SDK | `X-TypeSafe-SDK` | `systemone` | `{"models":[{name,description,release_date}]}` |
| Anything else | Default | `chat_completions` and `responses` | `{"object":"list","data":[{id,object,created,owned_by}]}` |

A native model serves an endpoint when a runner advertises it, an operator approved it, and supply
is fresh with free capacity. A runner's claim alone never adds a model to a list. A model at full
capacity leaves the lists until capacity returns.

The OpenAI row requires both endpoints because one SDK covers chat and Responses, and a list
request does not say which the caller will use. A chat-only native model therefore leaves the
OpenAI list but stays callable by name.

Validated routes appear for OpenAI and Anthropic, because the gateway translates them, and never
for System One. A runner that doesn't declare the route's upstream profile serves nobody on that
route, both when listing and when selecting a runner to pay. A Clef runner registers as `decision`,
so a Clef route that forgets `mode: native` serves nobody, rather than appearing to chat clients.

The list is a point-in-time filter on API compatibility, not a feature guarantee: a text-only model
can be listed for a client that later sends images. Calling a model on an endpoint with no approval
returns 400 before payment. See the [System One contract](system-one.md).

Registry mode also requires an active retail price, and the key needs inference permission in every
view. Listing dispatches no paid jobs. Models are deduplicated and sorted. The headers select a view
and cannot widen access, because a forged header only reshapes what the key may already see.
Responses, including errors, carry `Vary: anthropic-version, x-typesafe-sdk`.

The Anthropic view accepts `x-api-key` or Bearer and uses Anthropic's error envelope. It returns one
complete page: `limit`, `after_id` and `before_id` fail with 400. Other views use Bearer.

Routes can supply `display_name`, `description` and `release_date` (`YYYY-MM-DD`). Without them, the
display name is the model name, the description is empty and the date is `1970-01-01`, which, like
`created: 0`, means unknown. No value is inferred from discovery.

Readiness is true when at least one approved model can serve at least one endpoint; an unavailable
model returns 503 independently.

## Request subset

Use Bearer authentication and a public model identifier. For example:

```json
{
  "model":"gemma4:26b",
  "input":"What is 17 times 23?",
  "max_output_tokens":128,
  "stream":true,
  "store":false
}
```

`input` accepts a string or a full-history array of messages, function calls and function outputs.
Messages accept text strings, `input_text` / `output_text` parts, and `input_image` URLs or data
URLs on a configured vision route. File IDs and audio inputs are not supported by this endpoint.
Function definitions use `type:function`, `name`, `description`, `parameters` and optional
`strict` (boolean, default false). Tool choice supports auto/none/required/named function; parallel calls require
route support. The gateway does not execute client functions. Return `function_call_output` with
its matching `call_id` and string `output`, together with the preceding conversation and calls.
Tool calls must precede their results; unresolved or unmatched tool results are rejected.

Additional supported fields: `instructions`, `temperature`, `top_p`, and `text.format` with plain
text, JSON object or the existing supported JSON schema subset. `strict:true` validates each
function's parameter schema before dispatch and enforces generated arguments independently of the
runner. Each object must set `additionalProperties:false` and require every declared property;
optional values use nullable types or nested `anyOf`. Non-recursive local `$ref` references
to `$defs` and `definitions` are supported with the [structured schema limits](extended-generation.md#structured-output).
Referenced objects must meet the same strict rules. Unsupported schema keywords fail before
payment. Schemas are never silently weakened. Omitted/false `strict` retains best-effort schema
behavior (arguments must still be a JSON object). This default is deliberately unchanged from
previous gateway releases, even though OpenAI Responses may normalize omitted strictness.

Strict tools and structured final text can be requested together. A tool-only turn is validated
against tool parameter schemas, without requiring a nonexistent JSON text answer. Any final text
is independently checked against `text.format`. The fake generates schema-valid arguments for
strict tools unless explicit argument fixtures are supplied.

Streaming argument deltas remain provisional. A call failing its strict schema never emits
argument/item completion or response success. The gateway drains the bounded stream under existing
deadlines to collect terminal usage, records usage once with a failed outcome, and returns
`unsupported_response`. Cancellation still closes the backend. Clients should execute calls only
after their completion event, never from incomplete argument deltas.

Omitted `store` defaults to **false**, a deliberate difference from OpenAI's storage default.
Only `store:false`, `background:false` and null/omitted `previous_response_id` are accepted.
Stored retrieval/deletion, conversation IDs, background execution, hosted tools, reasoning output,
annotations and generated media are outside this subset. Unknown request fields fail with 400
before paid dispatch; unsupported runner output fails with `unsupported_response`.

## Output and streaming

Nonstream output is a `response` object with response ID, model, status, typed `output` items and
measured usage. Assistant items contain `output_text`; function-call items contain distinct item
and call IDs, name and JSON argument string. Input/cached/output counts are preserved. The SDK's
`output_text` helper assembles message text. IDs identify this request's output; they do not imply
stored response retrieval.

SSE includes response creation/progress, item/content starts, text or function argument deltas,
corresponding completion events, and a final `response.completed` with usage. Events carry increasing
sequence numbers. Token-limit termination uses `response.incomplete` and `max_output_tokens`;
errors close without a success terminal. Connection loss and deadlines close the owned generator
and notify the engine. Error frames never expose raw runner exceptions or response bodies.

## Upstream transport

`upstream_api: "chat_completions"` (default) translates this stateless subset through the existing
chat runner. `upstream_api: "responses"` dispatches to the runner's native `/v1/responses` through
Builder Core. Both upstream paths request SSE even for a nonstream client, and share accounting,
settlement, job correlation, deadlines and cleanup. No unmetered HTTP proxy is introduced.

Native Responses currently supports text, image input, functions, structured text, temperature
and top-p. Stop, seed/penalties/top-k, reasoning/history/budgets and audio are rejected. Native
streaming must provide increasing `sequence_number`, stable response/item identities, matching
start/delta/done lifecycle, matching final output, and integer input/output/total usage. Invalid,
missing or unsupported events fail explicitly. The response decoder bounds output bytes and items.

The public Responses protocol is recorded under the existing OpenAI accounting family. Wholesale
records retain the actual engine job/path. Retail usage is recorded once; settlement/reporting does
not re-run inference. Failure after a paid request does not erase its wholesale payment evidence.

## Verification and rollout

Offline SDK and real-engine tests cover both upstream paths, normal/streaming output, tools,
images, structured text, truncation, malformed output, model filtering, authentication, prices,
usage/job correlation and generator cancellation. Live Gemma acceptance requires its actual route.
Runners move to runner contract v1 together with the gateway: a v1 gateway ignores runners still
registered in an earlier metadata format.

See [setup and validation](../OPERATIONS.md#models-and-responses-rollout).
