# Native model inputs

Native routes forward the original JSON body. A backend must support both the selected
model's input modality and the endpoint's representation of it. The gateway does not
fetch URLs, decode media, run OCR, extract PDFs or translate one provider's media format
into another. Usage comes from the backend, including its image-token accounting; the
gateway does not estimate image tokens from dimensions or bytes.

## Image requests

For Chat Completions, put text and image parts in one message's content array:

```json
{
  "model": "your-approved-vision-model",
  "messages": [{
    "role": "user",
    "content": [
      {"type": "text", "text": "Describe this image."},
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,BASE64_BYTES"}}
    ]
  }],
  "max_tokens": 128
}
```

Send this JSON to `/v1/chat/completions` with the gateway bearer key. Replace the model
with its configured public name and BASE64_BYTES with the encoded PNG. For streaming,
add `stream: true` and `stream_options: {"include_usage": true}`.

Responses uses `input` messages containing `input_text` and
`{"type":"input_image","image_url":"data:image/png;base64,..."}`. Send to `/v1/responses`
and set `store: false`; `stream: true` enables streaming.

Anthropic Messages uses `messages` with text and
`{"type":"image","source":{"type":"base64","media_type":"image/png","data":"..."}}`.
Send to `/v1/messages`, supply `max_tokens`, and authenticate with the gateway `x-api-key`.
The backend must support the Anthropic representation; a compatible Chat endpoint alone
is not enough.

The whole JSON request is limited to 8 MiB. Base64 expands binary data by roughly one
third; keep images comfortably below that limit and any smaller proxy/backend limit.
A local filesystem path is not a portable image reference. The gateway does not provide a
file-upload or file-ID service. An HTTP image URL, where supported, is fetched by the
backend and requires backend egress/domain restrictions; the image acceptance fixture uses
inline PNG bytes, not arbitrary remote URLs. vLLM documents `--allowed-media-domains` and
`VLLM_MEDIA_URL_ALLOW_REDIRECTS=0` for controlling those fetches.

## Other inputs

| Input | Representation and current boundary |
| --- | --- |
| Text, code, JSON and conversation history | Ordinary text/message content; supported by the tested text routes. These formats do not require separate model modalities. |
| Tool results | Provider-specific tool-result messages/items; tested separately in the Qwen3.8 follow-up. The gateway does not execute tools. |
| Images/screenshots/scanned pages | Vision model plus provider image content blocks; see the image acceptance report. |
| Audio | Some models/runtimes accept inline audio or audio URLs in chat. Requires separate model/endpoint/usage acceptance; not certified by image tests. Dedicated `/v1/audio/*` endpoints are not exposed by this native gateway. |
| Video | Some vision models/runtimes accept video URLs or frame sequences. Requires separate acceptance, frame/size/time limits and suitable model configuration; not certified here. |
| PDFs and other files | Extract text or render pages into images in the client/application, or use a separately verified backend file-content API. Generic PDFs, uploaded file IDs and document parsing are not part of this gateway contract. |
| Precomputed embeddings/tensors | Backend-specific advanced input formats; not certified. The gateway has no native `/v1/embeddings` endpoint or tensor-upload service. |

Only Chat Completions, Responses and Messages are admitted native generation endpoints.
Passing an unknown JSON field unchanged does not certify that a backend understands it.
Endpoint approval remains separate from feature/modality evidence; automatic modality-aware
selection is not implemented. Bind evidence to the exact deployed model, runtime and revision.

Primary runtime references:

- [vLLM multimodal inputs](https://docs.vllm.ai/en/latest/features/multimodal_inputs/): images, audio, video and model-dependent representations.
- [SGLang multimodal models](https://github.com/sgl-project/sglang/blob/main/docs/docs/supported-models/multimodal_language_models.mdx).
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility): vision requests and endpoint-specific support.

[Image acceptance](../../live-runner/tests/vision-acceptance-2026-10-27.md) verifies inline
PNG inputs on the pinned vLLM/Qwen3.8 deployment through all three native endpoints.
