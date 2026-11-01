# System One decisions

A decision model reads a state and a set of typed questions, then returns a probability for each
allowed answer. It generates no text. Cloudflare Clef is one example. SGLang serves these models
at `POST /v1/systemone`, using the request and response shape of TypeSafe's System One API, and
Clef refuses chat and generation requests.

The gateway forwards this route natively. It authenticates, admits, pays for and meters each
request, and leaves the body to the backend. Nothing translates between decisions and chat.

Status: implemented and tested offline through `typesafe-sdk` 0.7.5. It has not yet run against a
live Clef server; the [plan](../exec-plans/active/system-one-route.md) lists the remaining paid
acceptance. SGLang behavior below comes from its v0.5.22 source.

## Send a request

Post JSON with a Bearer key. A native route must be configured for the model.

```json
{
  "model": "Cloudflare/clef-flash",
  "state": "The parcel arrived damaged.",
  "questions": {
    "needs_help": {"type": "noul", "instructions": "Does the customer need help?"}
  }
}
```

The response gives the probability of true for a `noul` question:

```json
{
  "model": "Cloudflare/clef-flash",
  "answers": {"needs_help": {"type": "noul", "noul": 0.91}},
  "usage": {"input_tokens": 160, "output_tokens": 0}
}
```

## Configure the TypeSafe SDK

The SDK works against the gateway once three defaults change:

```python
from typesafe_sdk import RetryPolicy, TypeSafeClient

client = TypeSafeClient(
    base_url="https://your-gateway",  # The server root, like the Anthropic SDK.
    api_key=gateway_key,
    model="Cloudflare/clef-flash",  # The default, jev-latest, is not a gateway model.
    retry=RetryPolicy(max_retries=0),
    compression=False,
)
```

- **Retries.** The SDK retries timeouts, connection errors, 408, 429 and every 5xx up to twice, and
  it never reads `x-should-retry`. Each attempt is a separate paid job, so one call can pay three
  times. Its default timeout is 10 seconds.
- **Compression.** On Python 3.14 or with the `zstd` extra, the SDK compresses bodies of 1 KiB or
  more. The gateway rejects compressed bodies before payment, because it must read `model`.

## What the gateway rejects

The gateway inspects only what would otherwise change routing, accounting or the answer without
warning. Each of these fails before payment:

| Request | Status | Reason |
| --- | --- | --- |
| `stream: true` | 400 | SGLang ignores the flag and returns JSON, which would read as a broken stream after payment |
| `images` | 400 | SGLang opens local file paths and fetches any URL placed here |
| `videos`, `assets`, `media_kwargs` | 400 | SGLang's route has no such fields and ignores them, so the decision would not use them |
| Compressed body | 400 | The gateway cannot read `model` |
| Model without a native route | 400 | A chat body posted here never runs as chat |
| Model with no `systemone` approval | 400 | Permanent until configuration changes |
| Approved model without capacity, or an expired approval | 503 | Temporary |

The backend owns everything else. An invalid question returns its 422 with a `detail` list, and
other refusals return 400. The gateway preserves both, adds `x-should-retry: false` and charges the
customer nothing, though the paid attempt still costs wholesale. SGLang ignores unknown top-level
fields, so a misspelled optional field does not fail.

## Long state is cut without warning

SGLang keeps the questions and cuts the end of `state` to fit the prompt limit. The limit is the
lower of 16,384 tokens and what the server's context allows. The response does not report the cut,
so the only signal is `usage.input_tokens` reaching the limit. If the questions alone do not fit,
the request fails.

The renderer passes `context_tokens` as the server's context length, so set it well above 16,384.
The [example manifest](../../live-runner/runtimes/sglang/clef-flash.example.json) uses 32,768.

## Usage and prices

Retail charges `usage.input_tokens` at the model's input rate. Output tokens are always zero, so the
output rate never applies. Input tokens include SGLang's prompt template and every option
description. By character count, the request above comes to about 150 to 170 tokens.

Retail is per token, but wholesale can be a flat price per paid attempt. At a fixed $0.001 per
attempt, an input rate of $0.50 per million breaks even only at 2,000 tokens, so a short decision
loses money at that rate. Set Clef's rate from real token counts. The gateway has no per-call fee.

## Deploy

Deploy the gateway first: older gateways reject metadata with an unknown profile.

1. Render a bundle from the example manifest with a real Hugging Face commit. Rendering records a new
   deployment revision, so re-rendering any runner after this change needs new approvals.
2. Add a native route. `mode` defaults to `validated`, which cannot serve this route:

   ```json
   {"name":"Cloudflare/clef-flash","app":"inference/sglang-clef","mode":"native",
    "description":"Answers typed questions with probabilities.","release_date":"2026-10-09"}
   ```

3. Add an approval to `GATEWAY_NATIVE_APPROVALS` with `"endpoint":"systemone"`. See the
   [native contract](native-inference.md#configure-an-accepted-deployment).
4. Set a retail price, because the catalog and the route both require one in registry mode:

   ```sh
   gateway prices set --model Cloudflare/clef-flash --version clef-v1 --currency usd \
     --input-per-million 7 --output-per-million 0
   ```

   This rate is an example, not a quote.

Model lists show the model only to System One clients; see
[model lists](models-and-responses.md#model-lists).
