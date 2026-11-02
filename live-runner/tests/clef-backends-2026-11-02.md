# Clef-Flash on four backends

Gateway v0.2.1 serves Clef-Flash decisions, with and without images, on SGLang, vLLM, llama.cpp and
Ollama. Each backend passed all 37 checks through the gateway on 2 November 2026. No payment was
involved: the node ran offchain, so tickets, wholesale costs and reconciliation remain unverified.

## Setup

Each backend started from [its compose profile](../runtimes/clef/compose.yaml) on one 24 GB RTX 4090.
Requests went from a client on the same host through the `inference-gateway:v0.2.1` image, with its
real Builder Core engine and native route, to the `inference-node:v0.2.0` image, then to the backend.

| Backend | Image version | GPU memory | Time to ready |
| --- | --- | --- | --- |
| SGLang | 0.5.22 | 21.9 GB | 52 s |
| vLLM | 0.33.0 | 20.8 GB | 105 s |
| llama.cpp | build 12033 | 20.9 GB | 15 s |
| Ollama | 0.40.11 | 13.7 GB after loading | 5 s, then 35.8 s for the first decision |

The test images were solid red and solid blue, as PNG and JPEG. These show that an image reaches the
model. They do not measure how well the model reads real photographs.

## Results through the gateway

| Check | Result on each backend |
| --- | --- |
| The model appears in the System One list and not in the OpenAI or Anthropic lists, including through the SDK | Pass |
| A text decision works over HTTP and through `typesafe-sdk` 0.7.5 | Pass |
| Each image form the backend accepts returns the right color, at 97.8–98.3% probability, with more input tokens than the control | Pass |
| Each image form the backend refuses returns the backend's error, with `x-should-retry: false` and no charge | Pass |
| An image sent through the SDK's `extra_body` returns the right color | Pass |
| 13 unsafe or ignored inputs return 400 and create no job | Pass |
| A bad question returns the backend's 4xx unchanged, with no charge | Pass |
| 12 image decisions, four at a time, all return the right color; the slowest took 0.3 s | Pass |
| One charge per successful decision, at the tokens the client saw, at the input rate only | Pass |

The 13 refused inputs were `stream`, a remote URL, an object holding a remote URL, a local path, a
`file://` URL, a list mixing a good image with a path, an audio data URL in `images`, bare audio in
`images`, and the `audio`, `videos`, `files`, `assets` and `media_kwargs` fields.

A failed request is marked `not_billable` only when reconciliation next runs. Some test runs ended
first and left failed requests `pending`. None of those requests had a charge.

The first run also tested the compose file itself, which had not been run before. llama.cpp refused
`-m=value`, and that backend passed once each value became its own argument.

## Image forms

Input tokens rose from 151 without an image to 218 with one on SGLang and vLLM, and to 203 on
llama.cpp and Ollama.

| Entry in `images` | SGLang | vLLM | llama.cpp | Ollama |
| --- | --- | --- | --- | --- |
| `data:` string | Works | Works | Works | 400 |
| `{"url": <data: string>}` | Works | 400 | 400 | 400 |
| Bare base64 PNG | Works | 400 | 400 | Works |
| Bare base64 JPEG | 500 | 400 | 400 | Works |

SGLang returns 500 for a bare JPEG because the base64 starts with `/9j/`, so it reads the value as a
file path. The file does not exist, and no base64 text can climb out of that directory, because `.` is
not in the base64 alphabet.

## What backends do with locations and unknown fields

These calls went directly to the backends, and they explain the gateway's rule.

| Input | SGLang | vLLM | llama.cpp | Ollama |
| --- | --- | --- | --- | --- |
| Remote URL in `images` | Fetches it | Fetches it | Refuses | Refuses |
| Local path in `images` | Opens it; 400 or 500 reveals whether it exists | Refuses | Refuses | Refuses |
| `stream`, `assets` or an unknown field | Ignores it, returns 200 | 400 | Ignores it, returns 200 | Ignores it, returns 200 |

The SDK's `Image` helper and `models.list()` both fail against every backend directly, the first
because it sends a multipart upload. Through the gateway `models.list()` works, and an offline test
shows the upload gets a 400.

## Audio and video

These calls also went directly to the backends, because the gateway refuses these fields. With Clef-Flash:

| Field | SGLang | vLLM | llama.cpp | Ollama |
| --- | --- | --- | --- | --- |
| `audio` | Ignores it, returns 200 | 400 | Ignores it, returns 200 | Ignores it, returns 200 |
| `videos` | Ignores it, returns 200 | Works | 400 | 400 |
| `files` | Ignores it, returns 200 | 400 | Video works; audio returns 500 | Ignores it, returns 200 |

An ignored field leaves the token count unchanged, at 143 tokens in this test, so the response gives
no other sign that the model never saw the media.

Three other models were tried for audio:

- **d1-Omni-2B on llama.cpp** is the only tested setup where audio worked. The probability of "tone"
  was 0.574 for a tone clip, 0.035 for silence and 0.155 with no audio. It works through `files`, and
  through `images` with a `data:audio/wav` string. The `audio` field is ignored here as well.
- **Gemma 4 E2B on vLLM** returns 400 for audio and video on this route.
- **Clef-Omni INT4 on vLLM** (`dbirks/clef-omni-int4`, a community quantization) fails to load at its default
  length and loads with `--max-model-len 16384`, at 23.3 GB. Every decision then returns 500, even with no media, because
  vLLM's quantization layer raises `NotImplementedError` for the embedding this model's decision path
  uses. The official Clef-Omni needs about 60 GB of GPU memory and does not fit.

So audio on vLLM remains untested, and the only working audio result is from one small model on
llama.cpp. This is why the gateway keeps refusing these fields.

## Chat images, for comparison

On 1 November 2026, chat image input passed 8 of 8 paid requests through `inference-gateway:v0.2.0`
against a vLLM Qwen3.8 runner: red and blue, JSON and SSE, on a native route and on a validated route
with `media_input`. That check did not confirm retail charges. System One's rule does not change chat
routes.

## Not covered

- Payment: tickets, wholesale cost and reconciliation for a decision.
- Behavior when `state` is too long, on any backend but SGLang, where it comes from source.
- Sustained load and the capacity limit of each backend.
- Any model other than those named here, and any other backend version.
