# vllm deployment candidate

Copy [manifest.example.json](manifest.example.json) into `live-runner/local/`, supply the
real model revision and configure the model/parser fields. Follow the [bundle guide](../../README.md).
The example is intentionally not runnable until a real model pin is supplied.

Set `model.source` to the Hugging Face repository and `model.revision` to an immutable
40-character commit. The renderer passes that revision and the public served-model name
to the runtime. Context limits come from the same manifest. Tool/reasoning parsers are
explicit optional fields; choose parsers appropriate to the selected model and test them.
The small example model is a configuration illustration, not a strict-tools benchmark.

The renderer enables `--enable-prompt-tokens-details` so Chat usage reports cached input
tokens. Streaming clients must still request `stream_options.include_usage: true`.
Absent or null optional detail objects count as no reported cached tokens; input/output
totals remain required. The gateway does not infer cache savings from missing fields.

The image is locked in [images.lock.json](../../images.lock.json). Registry resolution is
verified. The [remote GPU report](../../tests/gpu-acceptance-2026-10-27.md) records
limited startup/endpoint acceptance and backend-specific gaps. Chat is the only default endpoint declaration. Responses and Messages may be
added as acceptance targets, not as automatic claims based on the runtime family name.
