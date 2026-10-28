# sglang deployment candidate

Copy [manifest.example.json](manifest.example.json) into `live-runner/local/`, supply the
real model revision and configure the model/parser fields. Follow the [bundle guide](../../README.md).
The example is intentionally not runnable until a real model pin is supplied.

Set `model.source` to the Hugging Face repository and `model.revision` to an immutable
40-character commit. The renderer passes that revision and the public served-model name
to the runtime. Context limits come from the same manifest. Tool/reasoning parsers are
explicit optional fields; choose parsers appropriate to the selected model and test them.
The small example model is a configuration illustration, not a strict-tools benchmark.

The image is locked in [images.lock.json](../../images.lock.json). Registry resolution is
verified. The [remote GPU report](../../tests/gpu-acceptance-2026-10-27.md) records
limited startup/endpoint acceptance and backend-specific gaps. Chat is the only default endpoint declaration. Responses and Messages may be
added as acceptance targets, not as automatic claims based on the runtime family name.

The renderer enables `--enable-cache-report` to expose cached input tokens in usage.
Streaming callers must still explicitly request usage under the chat endpoint profile.
