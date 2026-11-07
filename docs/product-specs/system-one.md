# System One decisions

A decision model reads a state and a set of typed questions, then returns a probability for each
allowed answer. It generates no text. Cloudflare Clef is one example. SGLang, vLLM, llama.cpp and
Ollama all serve these models at `POST /v1/systemone`, using the request and response shape of
TypeSafe's System One API, and Clef refuses chat and generation requests.

The gateway forwards this route natively. It authenticates, admits, pays for and meters each
request, and leaves the body to the backend. Nothing translates between decisions and chat.

Status: tested through the gateway on all four backends, with Clef-Flash on a GPU, on 2 November 2026;
see the [backend comparison](../../live-runner/tests/clef-backends-2026-11-02.md). The node ran offchain,
so no decision has been paid for yet; the [plan](../exec-plans/active/system-one-route.md) lists that
remaining acceptance.

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

## Send an image

Add an `images` list. Each entry must contain the image itself, in one of three forms:

```json
{
  "model": "Cloudflare/clef-flash",
  "state": "Is the parcel in this photo damaged?",
  "questions": {"damaged": {"type": "noul", "instructions": "Is it damaged?"}},
  "images": ["data:image/png;base64,iVBORw0KGgo..."]
}
```

- A `data:image/<type>;base64,` string.
- Bare base64, without the prefix.
- An object, `{"url": <either of the above>}`, with an optional string `detail`.

The bytes must decode to a PNG, JPEG, GIF or WebP. The whole request is limited to 8 MiB, and base64
adds about a third to the size of an image.

The gateway checks only that the entry carries an image rather than a location. It does not choose or
convert the form, so the client must send one that its backend accepts:

| Backend | `data:` string | `{"url": ...}` | Bare base64 |
| --- | --- | --- | --- |
| SGLang 0.5.22 | Yes | Yes | PNG; a JPEG returns 500 |
| vLLM 0.33.0 | Yes | No | No |
| llama.cpp build 12033 | Yes | No | No |
| Ollama 0.40.11 | No | No | Yes |

A `data:` string works everywhere except Ollama. When a backend refuses a form, the client receives
its error unchanged and pays nothing. These results are for Clef-Flash; see the
[backend comparison](../../live-runner/tests/clef-backends-2026-11-02.md).

To confirm that an image reached the model, compare `usage.input_tokens` with and without it. An
ignored image returns a normal answer with the same token count.

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
- **Images.** Pass them with `extra_body`. The SDK's `Image` helper sends a multipart upload, which
  no tested backend accepts, so the gateway refuses it:

  ```python
  client.system_one(
      state="Is the parcel in this photo damaged?",
      questions={"damaged": Noul(instructions="Is it damaged?")},
      extra_body={"images": ["data:image/png;base64,iVBORw0KGgo..."]},
  )
  ```

## What the gateway rejects

The gateway inspects only what would otherwise change routing, accounting or the answer without
warning, or reach something the client should not. Each of these fails before payment:

| Request | Status | Reason |
| --- | --- | --- |
| `stream: true` | 400 | Decisions have no stream; a backend that ignores the flag answers with JSON, which would read as a broken stream after payment |
| Any top-level field but `model`, `state`, `questions` and `images`, including `stream: false` | 400 | The `typesafe.systemone.v1` entry's request schema allows no others, so a misspelled field can't pass unnoticed |
| An `images` entry that is not an inline image | 400 | SGLang opens local paths, and SGLang and vLLM fetch URLs |
| `audio`, `videos`, `files`, `assets`, `media_kwargs` | 400 | Backends differ, and some ignore these and answer anyway, so the customer would pay for a decision that never saw the media |
| Compressed body | 400 | The gateway cannot read `model` |
| Model without a native route | 400 | A chat body posted here never runs as chat |
| Model with no `systemone` approval | 400 | Permanent until configuration changes |
| Approved model without capacity, or an expired approval | 503 | Temporary |

The backend owns everything else. An invalid question returns the backend's error, usually a 422 with
a `detail` list. The gateway preserves it, adds `x-should-retry: false` and charges the customer
nothing, though the paid attempt still costs wholesale. A misspelled top-level field fails before
payment, because the entry's schema allows none it doesn't define.

### Why audio and video are refused

Only some model and backend pairs can use them, and the failure is silent. With Clef-Flash, SGLang,
Ollama and llama.cpp all answer a request that has an `audio` field as if the field were absent. The
image rule has no such gap: all four backends use `images`. The field name also says nothing about the
content, since llama.cpp passes a WAV sent through `images` to a model that can hear it. So the gateway
checks the bytes.

The [backend comparison](../../live-runner/tests/clef-backends-2026-11-02.md) lists what each backend
did. Allowing audio or video needs a per-route setting that names the media a deployment has proved it
uses; the [debt tracker](../exec-plans/tech-debt-tracker.md) records this.

## Long state is cut without warning

SGLang keeps the questions and cuts the end of `state` to fit the prompt limit. The limit is the
lower of 16,384 tokens and what the server's context allows. The response does not report the cut,
so the only signal is `usage.input_tokens` reaching the limit. If the questions alone do not fit,
the request fails. The other backends' behavior has not been measured.

The [SGLang configuration](../../live-runner/runtimes/clef/compose.yaml) sets a context of 17,000
tokens to clear that limit.

## Usage and prices

Retail charges `usage.input_tokens` at the model's input rate. Output tokens are always zero, so the
output rate never applies. Input tokens include the backend's prompt template and every option
description. By character count, the request above comes to about 150 to 170 tokens on SGLang. An image
adds to the count, and backends count the same request differently.

Retail is per token, but wholesale can be a flat price per paid attempt. At a fixed $0.001 per
attempt, an input rate of $0.50 per million breaks even only at 2,000 tokens, so a short decision
loses money at that rate. Set Clef's rate from real token counts. The gateway has no per-call fee.

## Deploy

Deploy the gateway first. A runner advertising `systemone` needs v0.2.0 or later on SGLang and v0.2.1
or later on any other backend.

1. Start a backend and register it with a node under App ID `decision`, following
   [Run Clef-Flash](../../live-runner/runtimes/clef/README.md).
2. Add a native route for the `decision` capability. `mode` defaults to `validated`, which cannot
   serve this route:

   ```json
   {"name":"Cloudflare/clef-flash","capability":"decision","mode":"native",
    "description":"Answers typed questions with probabilities.","release_date":"2026-10-09"}
   ```

3. Add an approval for profile `typesafe.systemone.v1`, with the runner's passing conformance
   report, to the file named by `GATEWAY_APPROVALS_FILE`. See
   [admission and approvals](runner-contract.md#admission-and-approvals).
4. Set a retail price, because the catalog and the route both require one in registry mode:

   ```sh
   gateway prices set --model Cloudflare/clef-flash --version clef-v1 --currency usd \
     --input-per-million 7 --output-per-million 0
   ```

   This rate is an example, not a quote.

Model lists show the model only to System One clients; see
[model lists](models-and-responses.md#model-lists).
