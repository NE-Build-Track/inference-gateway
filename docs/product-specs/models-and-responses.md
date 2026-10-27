# Model discovery and stateless Responses

This contract adds OpenAI-compatible `GET /v1/models` and `POST /v1/responses`.
It is a documented subset, not the entire OpenAI Responses service. Existing Chat Completions
and Anthropic Messages remain available. The model-list endpoint uses OpenAI authentication
and response formatting; Anthropic model-list pagination/authentication is not implemented.

## Model identity and routing

Runner discovery metadata must be a JSON object containing a nonempty string `model_name`:

```json
{"model_name":"gemma4:26b"}
```

That exact string is the public model name and upstream request `model`. App names identify
applications, never model identities. Invalid/missing metadata excludes a runner. There is no
fallback to an app suffix or default model. Existing routes using public aliases must migrate.
`upstream_model` is retained only when it equals `name`; differing aliases fail configuration.

Operators explicitly approve model/app pairs through `GATEWAY_MODEL_ROUTES`. Duplicate model
names across different apps are allowed; duplicate pairs are rejected. The gateway chooses the
eligible app with the most available runner capacity among routes supporting the request.
An injected Builder Core `SelectionPolicy` filters candidates by exact model metadata and positive
(or unknown) capacity, then delegates ordering to the existing policy. This also filters every
retry candidate within the chosen app. No cross-app retry is introduced, and paid attempts retain
the engine's existing retry restrictions. Builder Core itself is unchanged.

Endpoint and feature support remain explicit operator declarations per model/app route. Metadata
`model_name` alone does not advertise Responses, tools or vision. Every matching runner within a
route must satisfy its declared contract. Heterogeneous endpoint capabilities within one model/app
pair require a future capability metadata contract; do not group incompatible runners together.

The authenticated model catalog contains approved models with fresh eligible supply. Registry
mode additionally requires an active retail price; the key must have inference permission.
Listing does not dispatch paid jobs. Models are deduplicated and sorted. `created: 0` means the
creation timestamp is unknown; no timestamp is inferred from discovery. Readiness is true when
at least one approved model has eligible supply; an unavailable model returns 503 independently.

```json
{"object":"list","data":[{"id":"gemma4:26b","object":"model","created":0,"owned_by":"livepeer"}]}
```

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
optional values use nullable types or nested `anyOf`. Unsupported schema keywords fail before
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
The available Qwen discovery records inspected during this implementation lacked `model_name`;
they cannot qualify under this contract. Do not deploy this version onto those routes until their
metadata and matching route names are configured. Existing v0.0.1 deployments are unchanged.

See [setup and validation](../OPERATIONS.md#models-and-responses-rollout).
