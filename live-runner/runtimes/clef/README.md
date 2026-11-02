# Run Clef-Flash

Clef-Flash is a decision model: it answers typed questions with probabilities and refuses chat.
Four backends serve it at `POST /v1/systemone`. [compose.yaml](compose.yaml) has a profile for each,
with a pinned image and settings that fit a 24 GB GPU, and
[metadata.examples.json](metadata.examples.json) has the runner metadata to match.

## Choose a backend

The gateway treats all four the same. They differ in how much memory they need and in which image
format they accept.

| Backend | Version | GPU memory | Time to ready | Send images as |
| --- | --- | --- | --- | --- |
| SGLang | 0.5.22 | About 22 GB | About 50 s | `data:` string, `{"url": ...}` or bare base64 PNG |
| vLLM | 0.33.0 | About 21 GB | About 105 s | `data:` string |
| llama.cpp | build 12033 | About 21 GB | About 15 s | `data:` string |
| Ollama | 0.40.11 | About 14 GB (8-bit weights) | At once; the first request loads the model in about 35 s | Bare base64 |

Clients must use a format the backend accepts. The gateway forwards each image unchanged, and it
returns the backend's refusal without charging for it. The
[System One contract](../../../docs/product-specs/system-one.md#send-an-image) has the details, and
the [backend comparison](../../tests/clef-backends-2026-11-02.md) has the evidence.

SGLang needs the limits in `compose.yaml`: at its default memory settings it crashes at startup on a
24 GB card. Ollama serves the model as `clef-flash:9b`, so its route and metadata use that name.

## Start one

Create the volumes once. They are external, so removing the stack cannot delete a large download.

```sh
docker volume create clef-hf
docker volume create clef-ollama
```

SGLang and vLLM download the model on first start, without a token. Ollama needs a pull:

```sh
docker compose --profile ollama-pull run --rm ollama-pull
```

llama.cpp reads `Clef-Flash-BF16.gguf` and `mmproj-Clef-Flash-BF16.gguf` from a `gguf/` directory
in `clef-hf`. Download both from `ggml-org/Clef-Flash-GGUF`.

Then start one profile. Each needs most of the GPU, so run them one at a time:

```sh
docker compose --profile vllm up -d
```

| Profile | Port | Health path |
| --- | --- | --- |
| `sglang` | 30001 | `/health` |
| `vllm` | 30002 | `/health` |
| `ollama` | 30003 | `/api/tags` |
| `llamacpp` | 30004 | `/health` |

Every server listens on loopback only. Keep it that way: SGLang opens local files named in a request,
and SGLang and vLLM fetch URLs. The gateway refuses those values, but it cannot protect a backend that
clients can reach directly.

## Register it with a node

The bundle renderer cannot produce these configurations. It has no SGLang memory flags and no
llama.cpp command line, so you write the node's configuration by hand. On a node that shares the
backend's network:

```toml
# orchestrator.toml: let the node reach a loopback runner.
RunnerGrants = ["127.0.0.1:30002"]
HealthGrants = ["127.0.0.1:30002"]
```

```toml
# runners.toml
[[Runners]]
ID = "clef-flash-vllm"
App = "inference/clef"
RunnerURL = "http://127.0.0.1:30002"
Mode = "single-shot"
Status = "ready"
Capacity = 4
Metadata = "<the backend's entry from metadata.examples.json, as one JSON string>"
HealthURL = "http://127.0.0.1:30002/health"
HealthCode = 200
```

Before you use a metadata example, replace `deployment_revision` with a SHA-256 that changes whenever
the deployment does; the hash of your compose file works. Approvals bind to this value, so changing it
needs a new approval. `runner_id` must match the node's `ID`.

The live test ran each backend with a capacity of 4. That shows four requests at once work, not that
4 is the limit. llama.cpp runs with one slot and queues the rest.

Finish with the route, approval and price in the
[System One contract](../../../docs/product-specs/system-one.md#deploy).
