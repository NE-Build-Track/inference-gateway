# Extended conversational generation

The gateway and deterministic fake implement the contracts below. This is a tested subset,
not full OpenAI or Anthropic parity. The Livepeer adapter also supports structured output,
reasoning on/off, stop sequences and parallel tools when explicitly enabled per route.
Other capabilities still fail before dispatch. A runner probe is evidence about one route
at one time, not permission to enable a feature globally.

## Feature matrix

| Feature | Chat Completions | Anthropic Messages |
| --- | --- | --- |
| Reasoning request | `reasoning_effort` or `gateway.reasoning` | `thinking: {type: enabled, budget_tokens: 1024}` or `gateway.reasoning` |
| Reasoning output/history | `reasoning_content` extension, separate from `content` | `thinking` blocks, thinking and signature deltas |
| Sampling | `top_p`, `frequency_penalty`, `presence_penalty`, `seed`, `stop`; `gateway.top_k` | `top_p`, `top_k`, `stop_sequences` |
| Structured output | `response_format` with `text`, `json_object`, or `json_schema` | `output_config.format` with `type: json_schema` and `schema` |
| Cited sources | `gateway.sources`; output URL `annotations` | Text documents with `citations.enabled`; output character-location citations; `gateway.sources` for URL sources |
| Hosted search | `gateway.hosted_tools: [web_search]` | `tools: [{type: web_search_20250305, name: web_search, max_uses: 1}]` or gateway extension |
| Parallel client tools | `parallel_tool_calls: true` | `tool_choice: {type: auto, disable_parallel_tool_use: false}` |
| Image input | `image_url` content parts: HTTP(S) URL or base64 data URL | `image` blocks with URL or base64 source |
| Audio input | `input_audio: {data, format}` content parts; WAV or MP3 | Same `input_audio` block as a gateway extension |
| Audio output | `modalities: [text, audio]` plus `audio: {voice, format}`; WAV or PCM16 | `gateway.output_media: [audio]` extension |
| Image output | `gateway.output_media: [image]` extension | Same gateway extension |

These field names are grounded in the installed SDKs and the official
[Chat Completions request reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).
Gateway extensions are intentionally named and do not imply that the upstream provider
accepts the same fields. SDK callers can send extensions with `extra_body`.

Reasoning effort accepts `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, and `max`.
These are request values, not a claim that every model supports every effort. Anthropic
thinking budgets must be at least 1024 and below `max_tokens`. Adaptive/redacted thinking
and provider signature verification remain outside this subset. Historical signatures
are opaque strings that the gateway preserves.

Top-p is between zero and one; top-k is a positive integer. Penalties are between -2 and
2. Stop sequences are limited to four nonempty strings. Unknown fields, non-finite numeric
values and invalid shapes fail with HTTP 400. The fake is deterministic: sampling settings
are retained in the command, while only stop sequences affect its output. Stop markers are
excluded from text; Anthropic receives `stop_reason: stop_sequence` and the matched marker.

## Structured output

The supported schema subset is deliberately explicit:

- Each node has one `type`: object, array, string, integer, number, boolean, or null.
- Objects accept `properties`, `required`, and boolean `additionalProperties`.
- Arrays require an `items` schema. Nodes may include `enum`, `title`, and `description`.
- Root schemas must be objects. Nesting is limited to 16 levels. References, unions,
  patterns, numerical bounds and other keywords fail explicitly rather than being ignored.
- Chat Completions schema mode accepts `strict: true`; false is not implemented.

The fake builds a deterministic example or uses an injected `structured_output` fixture.
The service independently validates complete output before emitting a success finish.
A truncated response has `length`/`max_tokens` and may contain incomplete JSON; it is never
advertised as schema-valid. Structured output cannot combine with tools, cited sources,
media output or stop sequences in this subset. Reasoning may precede structured final text.

```json
{
  "model": "fake-model",
  "messages": [{"role": "user", "content": "Return a city"}],
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "city",
      "strict": true,
      "schema": {
        "type": "object",
        "properties": {"city": {"type": "string", "enum": ["Boston"]}},
        "required": ["city"],
        "additionalProperties": false
      }
    }
  }
}
```

## Citations and hosted tools

`gateway.sources` is an array of `{title, text, url}` records. URLs must be HTTP(S) and
contain no credentials. The gateway does not retrieve them. The fake quotes supplied text
and emits references to that source; it does not verify the source's factual content.
Anthropic text documents use `{type: document, source: {type: text, media_type: text/plain,
data: ...}, title: ..., citations: {enabled: true}}`. PDF documents are not implemented.
Character citation offsets refer to source documents; URL annotation offsets refer to
response text. Citations survive streaming and historical assistant messages.

Hosted search is distinct from client function calls. It returns a server call, a server
result and final text; clients must not execute the server call or provide its result.
The fake uses an explicitly synthetic example.com result and never accesses a network.
The one supported hosted tool is web search with one use. Code execution, fetching and
other hosted tools/options are rejected. Client tools cannot combine with hosted search,
cited documents/sources or media output in this subset.

Anthropic uses `server_tool_use` and `web_search_tool_result` blocks. Chat Completions uses
`message.gateway_blocks` (and `delta.gateway_blocks` in SSE), containing `{index, block}`
entries with the same block representation. Hosted call arguments are buffered until the
block closes on the OpenAI interface; client tool arguments stream incrementally. Historical
OpenAI assistant messages can send these entries back as `gateway_blocks`.

Document citations without URLs use `gateway_citations` on the OpenAI interface rather
than inventing a URL. Native URL annotations and document citations are separate fields.

## Media and interleaving

Image input supports PNG, JPEG, GIF and WebP declarations. Audio input supports WAV and MP3.
Base64 is validated and limited to 8 MiB of encoded data per part; this is not file decoding
or malware inspection. URLs are validated but never fetched by the gateway or fake.

The fake returns a transparent PNG pixel and 10 ms of silence, not generated image or
speech content. Audio output accepts WAV and PCM16; voice selection is carried in the command
but does not change silence. Chat Completions audio-only requests omit final text. Audio
output uses the `audio` field; its ID is a response identifier, not a retrievable stored asset.
Historical audio-ID lookup is not implemented.

Images on both interfaces, and audio on Anthropic, use the extension block
`{type: gateway_media, kind: image|audio, data: <base64>, media_type: ...}`. These are atomic
blocks rather than incremental binary streams. OpenAI places image blocks in `gateway_blocks`;
Anthropic places them in `content`. Historical extension media blocks are accepted. The
installed SDKs preserve them, but provider-specific convenience helpers may not understand
these extensions. `gateway.output_media` supports both image and audio on either interface.

Parallel client calls have distinct indices and IDs. Their argument fragments can overlap;
assembly and validation happen independently. Setting parallel calls false selects one fake
tool. The service limits output to 128 blocks and 8 MiB, allows overlap only between client
tool blocks, and checks closure and hosted call/result correlation. Cancellation closes the
backend generator on all paths. Nonstream output is ordered by block start index.

## Failure and verification status

| Condition | HTTP before streaming | Error code | Meaning |
| --- | --- | --- | --- |
| Backend adapter cannot serve requested feature/history | 400 | `unsupported_feature` | Rejected before paid dispatch |
| Unsupported or invalid complete backend feature output | 502 | `unsupported_response` | Public message is `Unsupported Response` |
| Timeout, capacity or transport failure | Existing 504/503/502 mapping | Existing error code | Operational failure; not capability evidence |

After response headers, unsupported output becomes an error frame and closes without a success
terminator. Errors keep protocol-specific types; the machine-readable `code` distinguishes the
new cases. Errors and logs do not include prompts, arguments, media, secrets or raw exceptions.

See the [Livepeer gap register](../exec-plans/livepeer-capability-gaps.md) and
[probe instructions](../OPERATIONS.md#capability-probes). Fake SDK tests prove the gateway's
wire contract. They do not establish model quality, real citation grounding, hosted execution,
media understanding, probabilistic sampling, or Livepeer support.


## Livepeer route capabilities

`GATEWAY_MODEL_ROUTES` accepts a `capabilities` array on each route. The default is empty,
which preserves the original text/tool subset. The supported names are `structured_output`,
`reasoning`, `stop`, `parallel_tools`, `sampling`, `reasoning_budget`, and `media_input`;
unknown names fail configuration validation.
Request requirements include historical content, so switching to a route cannot silently
remove reasoning or unsupported media from conversation history.

```json
[{
  "name": "qwen3.8-27b",
  "app": "blueclaw/chat/qwen3.8-27b",
  "disable_thinking": true,
  "capabilities": ["structured_output", "reasoning", "stop", "parallel_tools"]
}]
```

Enable a capability only for a runner whose behavior you have checked. Configuration is an
operator declaration, not automatic discovery. See the [gap register](../exec-plans/livepeer-capability-gaps.md)
for dated acceptance results and remaining verification limits.

- **Structured output:** forwards the supported JSON object/schema contract. The gateway still
  validates complete final text before success. Refusals or invalid complete output produce
  `unsupported_response`; `length` output may contain incomplete JSON. Schema mode has stronger
  checks than merely accepting a request field.
- **Reasoning:** use `extra_body={"gateway": {"reasoning": true}}` with either SDK. The route sends
  `chat_template_kwargs.enable_thinking=true`, overriding its default disabled setting. Both
  runner `reasoning` and `reasoning_content` deltas are supported before final text/tool calls;
  reasoning after final output is rejected. Anthropic thinking blocks carry an empty signature:
  this runner has no Anthropic signature. Unsigned reasoning history is forwarded in
  `reasoning_content`. Nonempty signatures remain unsupported. Effort levels require a separate
  `reasoning_efforts` allowlist on the route; they forward as `reasoning_effort`, without an
  invented effort-to-budget mapping. `reasoning_budget` enables native Anthropic thinking budgets,
  forwarding them as vLLM `thinking_token_budget`. Both require the `reasoning` capability.
  These controls remain disabled on the local Qwen deployment because application is unverified.
  Reasoning-only truncation is valid; a normal finish without final text or tools is an error.
- **Stop sequences:** supported for final-text requests without tools or enabled thinking.
  The adapter sends `stop` and vLLM's `include_stop_str_in_output=true`, holds possible marker
  prefixes between chunks, and strips the marker and any later text. It drains the bounded runner
  response to obtain actual usage and terminal evidence; usage is never recalculated from text.
  A matching runner `stop_reason` string also identifies the stop. Without either observation,
  natural termination remains `end_turn` on Anthropic; the gateway never guesses a sequence.
- **Parallel tools:** explicit opt-in forwards `parallel_tool_calls=true` and streams each
  identified call independently. Blocks are numbered by start order, not arbitrary runner indices.
  Invalid IDs, malformed arguments and truncated calls still fail. When not enabled/requested,
  the existing adapter normalizes incoming interleaving into sequential blocks for compatibility;
  the gateway does not claim it can force a model to choose two tools or only one tool.

Reasoning output and total completion usage are separate concepts: runner completion tokens may
include thinking. The gateway preserves the runner's aggregate usage and does not invent a
reasoning-token breakdown. Neither effort labels nor returned text establish budget enforcement.
The adapter follows the [vLLM reasoning request contract](https://docs.vllm.ai/en/stable/features/reasoning_outputs/);
operators must verify their deployed runner version and model configuration.

- **Sampling:** the `sampling` capability forwards provided `top_p`, `top_k`, `seed`,
  `frequency_penalty` and `presence_penalty` values, including zero. Unset fields remain absent.
  This is transport support; stochastic behavior is the runner's responsibility.
- **Media input:** `media_input` requires a nonempty `input_media` allowlist (`image`, `audio`).
  User content preserves text/media order. Images use native `image_url`, retaining inline MIME,
  URL and detail. Inline WAV/MP3 use native `input_audio`. Other audio formats, URL audio and
  assistant media history are rejected before dispatch. Enabling image input never enables audio.

These additional adapter paths have SDK/fake-engine coverage but remain disabled on the local
Qwen route. Citations, hosted execution and media output still require backend integration;
there are no route switches that pretend to enable those unimplemented Livepeer paths.
