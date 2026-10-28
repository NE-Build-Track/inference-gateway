# ollama deployment candidate

Copy [manifest.example.json](manifest.example.json) into `live-runner/local/`, supply the
real model revision and configure the model/parser fields. Follow the [bundle guide](../../README.md).
The example is intentionally not runnable until a real model pin is supplied.

The model name must equal the installed Ollama name, including its tag. Install the
model using the pinned server, obtain its actual manifest digest from the server's
model listing, and put `sha256:<digest>` in `model.revision`. Provisioning is operator
work: the renderer never pulls a mutable model silently. Warm/load the model and keep
it resident for service; readiness requires a matching entry in `/api/ps`. Parser flags
are rejected because Ollama owns its model-specific parsing. The configured context
setting applies through `OLLAMA_CONTEXT_LENGTH`; verify actual model behavior live.

The image is locked in [images.lock.json](../../images.lock.json). Registry resolution is
verified. The [remote GPU report](../../tests/gpu-acceptance-2026-10-27.md) records
limited startup/endpoint acceptance and backend-specific gaps. Chat is the only default endpoint declaration. Responses and Messages may be
added as acceptance targets, not as automatic claims based on the runtime family name.
