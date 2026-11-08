# Run Clef-Flash

Clef-Flash is a decision model: it answers typed questions with probabilities and refuses chat.
Four backends serve it at `POST /v1/systemone`. [compose.yaml](compose.yaml) has a profile for each,
with a pinned image and settings that fit a 24 GB GPU, and
[metadata.examples.json](metadata.examples.json) has a runner contract v1 envelope to match each.

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
App = "decision"
RunnerURL = "http://127.0.0.1:30002"
Mode = "single-shot"
Status = "ready"
Capacity = 4
Metadata = '<the backend's entry from metadata.examples.json, as compact JSON>'
HealthURL = "http://127.0.0.1:8080/healthz"
HealthCode = 200
[Runners.PriceInfo]
Price = 0.5
Currency = "usd"
Unit = "hour"
```

The App ID is `decision`, the capability System One's profile serves; a runner contract v1 gateway
selects nothing else for it. The price is an example; set the wholesale rate your node charges.

Before you use an envelope example, replace `deployment_revision` with the standard's recipe over the
backend's image digest, the model's source and revision, the profile `typesafe.systemone.v1` and the
settings that change behavior. `runner-contract envelope` computes it from a `runner.toml` manifest
([the standard's tool](https://github.com/NE-Build-Track/live-runner-standard/tree/main/python/runner-contract)).
Approvals bind to this value, so changing the image, model or settings needs a new approval, while
moving the node or changing capacity doesn't. `runner_id` must match the node's `ID`.

`HealthURL` must answer 200 only after a self-test of `systemone` passes, which a backend's own
`/health` doesn't do. Run the standard's `runner-contract ready` beside the backend on the
registration's block, installed as [readiness.txt](../../tooling/readiness.txt) pins it, and point
`HealthURL` at it:

```sh
runner-contract ready --block runners.toml --runner-id <ID> --engine-health http://<backend>/health
```

The live test ran each backend with a capacity of 4. That shows four requests at once work, not that
4 is the limit. llama.cpp runs with one slot and queues the rest.

Finish with the route, approval and price in the
[System One contract](../../../docs/product-specs/system-one.md#deploy).
