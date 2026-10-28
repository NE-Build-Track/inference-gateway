# Native image acceptance — 27 October 2026

Inline PNG image input works through the native gateway with the tested vLLM/Qwen3.8
configuration. No gateway application change was required. The added offline tests,
repeatable live probe and input guide make this an explicit acceptance case.

## Configuration and assertions

The runtime/model pins and launch arguments are those in the
[Qwen3.8 report](qwen38-followup-2026-10-27.md): vLLM 0.32.0,
`cyankiwi/Qwen3.8-27B-AWQ-INT4` at revision
`e3a700906e6a73ba4290b1873dc2ba8198527ab5`, served as `qwen3.8-27b` with a 4,096-token
context. That exact model configuration includes a vision encoder. We reused its persistent
HF cache on ai2-rig's RTX 5090; no replacement model was downloaded.

Gateway image:
`registry.livepeer.tools/ne-build-track/inference-gateway@sha256:cfd1bcdca6cbb8eb01eecee079f2636c9f432ed573038a3e8e535867e472548f`.
The retained paid node uses the previously tested direct-discovery image
`registry.livepeer.tools/ne-build-track/inference-node@sha256:be68e3776cf624949959482e2694bb73b5a4b0a8e7a251d25ad08bb04669e7c9`.
The newer signer-discovery fix is not exercised by this direct-discovery topology.

The probe generates 224×224 red and blue PNGs with identical prompts that list the same
possible color answers. The expected answer depends on image pixels, not a filename or
color-specific prompt. Temperature is zero and thinking is disabled. Request formats are
provider-native: Chat image_url data URLs, Responses input_image data URLs and Messages
base64 image source blocks. Raw request/response bodies and image data are not recorded.

| Check | Result |
| --- | --- |
| Chat, Responses and Messages × red/blue × unary/SSE × direct/gateway | 24/24 pass |
| Invalid image × three endpoints × direct/gateway | 6/6 return HTTP 400 |
| Successful paid gateway image requests | 12, each with exactly one retail charge |
| Rejected paid image requests | 3, unknown usage and no retail charge |
| Gateway/Core usage correlation and paid attempts | Matching usage, one paid attempt per tested gateway request |

Successful fixtures report 97 input and 2 output tokens. These counts include backend image
processing; they are not calculated from image dimensions by the gateway. Wholesale cost
observations and ticket finality are separate; see the sanitized JSON audit.

## Test corrections and limits

An initial rapid sequence encountered the known single-slot/discovery-cache 503 behavior.
The acceptance probe waits for model admission and spaces gateway submissions by 16 seconds;
it never automatically retries an inference request. This is not a throughput test.

The first Responses streaming classifier concatenated text deltas with repeated completed
text. Its false negative was fixed by extracting only text deltas, with an offline regression
covering repeated terminal text and split chunks. Responses was rerun; the consolidated
matrix uses that rerun and the passing Chat/Messages records.

This verifies inline PNG fixtures, not arbitrary photographs, OCR quality, JPEG/animated
formats, multiple images, remote-URL fetching, large images, video, audio or PDFs. Those need
separate acceptance before being advertised. The native gateway's whole-request limit remains
8 MiB. Endpoint admission does not automatically certify image capability for other models.

## Repeat and cleanup

Run [probe_vision.py](probe_vision.py) as described in the [test guide](README.md).
Offline CI additionally covers image-bearing JSON byte preservation, unary/SSE usage and
charges, backend rejection, request-size limits and stream text extraction.

The vision runtime container was stopped and removed from ai2-rig; all fourteen volumes
and its five existing services were retained. GPU memory returned to 2 MiB. The SSH tunnel
and local readiness server were stopped. The paid node remains with its inference runner
idle to redeem persisted tickets. No v0.0.1 image tag was changed.
