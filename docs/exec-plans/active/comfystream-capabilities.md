# ComfyStream capabilities

Acceptance: preserve v1 runner contracts and existing conversational routes; validate
descriptor bindings, tenant isolation, idempotency, cancellation and restart recovery.
Only advertise GPU behavior after acceptance on the complete immutable deployment.

The implementation is a candidate across Stream Pack, ComfyStream, this gateway and
runner examples. Descriptors own public parameters and workflow mappings. The core
embedded process executor owns CUDA work. Gateway adapters own persistent media jobs
and artifact authorization. Existing text accounting is unchanged; media usage is
stored with the job, but media charging and paid Livepeer dispatch remain unimplemented.

The initial gateway snapshot passed `make check` on eliteprd1 (642 tests plus lint,
formatting, types and docs). A CUDA 12.8 media image installed with uv/comfy-cli and
normal dependency resolution. ComfyStream is now based on `feat/ffmpeg-stream-batch`
at `02ca83c`; its FFmpeg/Swarm behavior and pricing are retained. Live transport uses
bounded shared-memory channels and the core process executor. Conversational text
execution is wired through the existing protocol adapters; rich Qwen features remain
explicitly unsupported pending conformance tests.

The updated gateway passed `make check` on eliteprd1: 645 tests, lint, formatting,
types and docs. Real OpenAI/Anthropic SDK tests cover the local conversational
adapter, image/transcription shapes, all-nine model discovery and OpenAPI references.
Six models have produced outputs through candidate ComfyStream sources; three large
checkpoints remain in acceptance. Native checkpoint reuse was measured with two
ACE-Step requests (27.8 seconds cold, 2.3 seconds warm); core dynamic VRAM offloads
between requests. Profile discovery authenticates the private transports and checks
descriptor digests before serving; HTTP disconnects cancel the execution handler.

Current limitations: all nine model adapters need exact-image GPU verification. V2
discovery and externally approved conversational selection are implemented; its
deployment renderer, media charging and paid media dispatch remain unimplemented.
Core executor progress forwarding,
shared admission across live and batch execution, and cancellation/VRAM/session cleanup
still need complete acceptance. Production ComfyStream remains unchanged until
release gates pass.

Do not label this execution plan completed until those acceptance gates pass.
