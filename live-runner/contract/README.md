# Bundle contract: runner contract v1

[deployment.schema.json](deployment.schema.json) describes operator input. It is generated from the
`Deployment` model in [render.py](../tooling/render.py), and a test fails if they drift. The renderer
also checks what JSON Schema alone can't: endpoint coherence, model revision type, duplicate
endpoints and HTTP origin semantics.

Everything a runner publishes follows the [live runner standard](https://github.com/NE-Build-Track/live-runner-standard),
pinned at registry release `2026.11.3`. The bundle defines no metadata format of its own. For each
registration, the renderer writes the standard's `runner.toml` manifest, and the standard's library
builds the envelope and the node's `[[Runners]]` block from it, then checks the block as the node
reads it.

## Endpoints, profiles and App IDs

| Endpoint | Profile | App ID |
| --- | --- | --- |
| `chat_completions` | `openai.chat.v2` | `chat` |
| `responses` | `openai.responses.stateless.v2` | `chat` |
| `messages` | `anthropic.messages.v2` | `chat` |
| `systemone` | `typesafe.systemone.v1` | `decision` |

Paths, framing, success rules, error mappings and work reports come from each profile's registry
entry, not from the bundle. An envelope names only the profile. A manifest listing endpoints of both
capabilities renders one registration per capability, sharing a `capacity_group`.

A profile is a test target, not a claim that a runtime passes it. Run `runner-contract conform`
against the running bundle before asking for an approval. The Responses profile is stateless: the
gateway refuses stored and background calls, and some backend versions store by default, so verify
the runtime's retention policy as part of acceptance.

## Model identity

One deployment serves one model. The envelope's `model.name` is what requests send. Its `source` is
`hf:<repository>` for vLLM and SGLang, or `ollama:<installed name>` for Ollama, and `revision` is the
Hugging Face commit or the Ollama manifest digest. There is no App ID fallback or alias rewrite.

## Runner identity without a node patch

The envelope's `runner_id` holds the node registration ID, because this node doesn't report it in
discovery; the standard allows the key for exactly that. When discovery omits its top-level
`runner_id`, the gateway fills it from the envelope before Core caches or selects runners. If both
are present they must match; a conflict excludes the runner. IDs are never guessed from App IDs, URL
paths or proxy hostnames. Transport continues to use the original advertised URL.

This is identification, not authorization. The gateway's approval binds the orchestrator origin,
runner ID, model, deployment revision, profile and expiry, and carries a conformance report. The
1,024-byte metadata limit includes `runner_id`; the renderer rejects an oversized envelope rather
than truncating it.

The bundle's node comes from the [image lock](../images.lock.json); its source is unmodified, and
node PR #1 is not required.
