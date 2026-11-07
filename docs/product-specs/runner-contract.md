# Runner contract v1

Status: implemented and verified offline. The gateway pins `livepeer-runner-contract` 0.3.0 and
registry release `2026.11.3`; live runners move to v1 in a coordinated cutover.

The gateway finds runners under the [live runner standard](https://github.com/NE-Build-Track/live-runner-standard),
runner contract v1. A runner registers with an App ID that names a capability, such as `chat`, and
publishes an **envelope** in its `Metadata`: the model it serves, and one endpoint per **wire profile**
it speaks. Each profile's behavior lives in an entry of the standard's registry. The gateway reads
those two and nothing else.

There is no compatibility with earlier formats. A runner whose metadata has `inference_contract`,
only `model_name`, or no `runner_contract: 1` is ignored.

## What the gateway serves

Each gateway endpoint speaks one profile, and each profile serves one capability:

| Endpoint | Profile | Capability |
| --- | --- | --- |
| `/v1/chat/completions` | `openai.chat.v2` | `chat` |
| `/v1/responses` | `openai.responses.stateless.v2` | `chat` |
| `/v1/messages` | `anthropic.messages.v2` | `chat` |
| `/v1/systemone` | `typesafe.systemone.v1` | `decision` |

A route names a capability, not an operator's App ID. `capability` defaults to `chat`:

```json
[{"name":"qwen3.8-27b"},
 {"name":"Cloudflare/clef-flash","capability":"decision","mode":"native"}]
```

A route's `name` is what clients send and what the runner's envelope gives as `model.name`. Startup
fails when a route's capability isn't on the standard's list, or when no gateway endpoint serves it.

## Which runners serve a route

Discovery keeps a runner only when all of these hold:

- Its orchestrator is on `GATEWAY_APPROVED_ORCHESTRATORS`, when that list is set.
- Its metadata is a valid `runner_contract: 1` envelope of at most 1,024 bytes. Unknown keys are
  ignored.
- Its App ID is the capability of a configured route.
- The node's runner ID and the envelope's `runner_id`, where both are present, are the same. When
  only the envelope names one, discovery fills it in. An ID is never inferred from a URL or App ID.

Discovery logs each runner it drops for its record once per process, with the reason and a link to
the standard. The reason quotes the schema, never the runner's values. A malformed, oversized or
deeply nested record excludes only its own runner; it never fails a discovery refresh.

At selection, before Builder Core ranks or pays, a runner must also:

- name the route's model in `model.name`;
- declare the endpoint with its profile, where the profile serves the route's capability;
- use the entry's default path and method. Core sends one path per job, so a runner whose
  envelope moves an endpoint is not selected;
- report free capacity, or no capacity figure where no approval is required;
- hold an approval where one is required.

## Admission and approvals

| Route | Needs an approval |
| --- | --- |
| Validated (translated) | Only a `private` runner, or one whose `admission` the gateway doesn't know |
| Native, including System One | Every runner |

An approval is operator evidence; nothing in a runner's metadata grants one. Put approvals in a JSON
array in the file named by `GATEWAY_APPROVALS_FILE`:

| Field | Meaning |
| --- | --- |
| `orchestrator` | The orchestrator's exact HTTP(S) origin |
| `runner_id` | The runner's ID |
| `model` | The route name, which equals the envelope's `model.name` |
| `deployment_revision` | The envelope's `deployment_revision` |
| `profile` | A registry entry ID, such as `openai.chat.v2` |
| `expires_at` | A future UTC Unix timestamp |
| `conformance` | The report `runner-contract conform` wrote for this runner, revision and profile |

The gateway refuses a file in which any report breaks its schema, didn't pass, or names another
runner, revision or profile. Reports aren't signed: an approval records that the operator accepted
the evidence, not that the runner proved it.

The gateway checks the file at most once a second and re-reads it when it changes. A file that becomes
unreadable or invalid keeps the last good approvals and logs `approvals_reload_failed`; at startup it
fails. Replacing the file revokes approvals for later selections; running jobs keep their settlement.
With no file, the gateway admits no native traffic and no private runner.

Produce a report where the tool reaches the runner directly, with no node or payment, then add it
to an approval. The [runner bundle](../../live-runner/README.md) writes a manifest and a block per
registration, naming the runtime and readiness URLs inside its Compose network; run the tool there,
or pass `--url` and `--health-url`:

```sh
uvx --from "git+https://github.com/NE-Build-Track/live-runner-standard.git@v0.3.0#subdirectory=python/runner-contract" \
  --with "livepeer-runner-registry @ git+https://github.com/NE-Build-Track/live-runner-standard.git@2026.11.3#subdirectory=python/runner-registry" \
  runner-contract conform --manifest qwen38-vllm.runner.toml --block qwen38-vllm.block.toml \
    --out report.json
```

## Requests and answers

The gateway checks every request against the entry's request schema before payment:

- On native routes, the client's body. A body that breaks the schema is a 400 that names where and
  what the schema wants, such as `at /tools/0: is missing name`, and nothing is paid. The message
  never quotes the request's values.
- On validated routes, the body the gateway translated. A route whose translation breaks the schema
  isn't used.

The check runs in a worker thread: it costs tens of milliseconds for a coding agent's request of
half a megabyte, and the event loop carries every other call's stream.

The System One entry allows no top-level fields beyond `model`, `state`, `questions` and `images`,
so `stream: false` and misspelled optional fields fail before payment. The chat, Responses and
Messages entries allow extension fields, which reach the runner unchanged. They also pass tools,
items and blocks of types they don't describe, and the runner decides: Codex's `custom` and
`web_search` tools, and Claude Code's server tools and `document` blocks. `anthropic.messages.v2`
takes the `system` messages Claude Code puts between turns.

An answer succeeds only when none of the entry's error mappings matches and its success rule holds.
A chat stream must end with `[DONE]`; a Responses answer must be `completed` or `incomplete`; a
Messages stream must end with an SSE event of type `message_stop`, as Anthropic sends it. The
gateway's own checks still apply on top: a native answer also needs usage, and a validated answer a
supported finish reason.

So a Responses answer cut by the caller's `max_output_tokens` is a success, as a chat answer that
ends in `finish_reason: "length"` is. On validated routes the gateway reports it as length-capped,
and still refuses an answer cut for another reason, such as a content filter, because it has no
finish reason for one. On native routes the bytes reach the client unchanged, and the ledger keeps
recording `incomplete` as a `failed` outcome with its measured usage, as it did before v1.

Builder Core owns retries. All four entries say the gateway owns attempts, so Core's failover after
a refusal is allowed.

## Known limits

- Builder Core keys concurrency limits, its offerings catalog and job records on App ID, so every
  chat model shares `chat` there.
- Core doesn't fail over on a 429, which the entries map to `busy`.
- The gateway doesn't enforce `capacity_group`; the runtime queues requests past its own limit.
- See the [debt tracker](../exec-plans/tech-debt-tracker.md).
