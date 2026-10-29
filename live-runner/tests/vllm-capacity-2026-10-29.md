# vLLM capacity benchmark — 29 October 2026

This benchmark decides what capacity the orchestrator should advertise for the supported
vLLM runner. On typical chat traffic, the runtime sustains 8 concurrent requests before
latency degrades; with long prompts, 4. The bundle currently advertises 1.

## Method

The runtime was the accepted configuration on a single RTX 5090 (32 GB): vLLM 0.32.0
(`vllm/vllm-openai@sha256:befccf0e…`), Qwen3.8-27B AWQ at revision `e3a7009`, and
`--max-model-len 4096`. The only change was `--max-num-seqs 32`, so that vLLM's own limit
would not hide the GPU's. vLLM reported a 52,565-token KV cache: room for 12.8 requests
that each use the full 4,096-token context.

`vllm bench serve` from the same image called the runtime's Chat Completions endpoint
directly, bypassing the node, payment and gateway. Prompts were random tokens with exact
input lengths. `--ignore-eos` forced every response to its full output length. Each
concurrency level ran at least six requests per slot. Goodput counts requests that met both
targets: time to first token at most 2 s and time per output token at most 60 ms.

| Profile | Concurrency | Requests/s | Output tok/s | Per-stream tok/s | TTFT p50 ms | TTFT p99 ms | TPOT p99 ms | Goodput |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Chat, 1,024 in / 256 out | 1 | 0.28 | 72 | 77.4 | 285 | 286 | 12.9 | 100% |
| | 2 | 0.53 | 135 | 69.8 | 153 | 177 | 14.4 | 100% |
| | 4 | 0.91 | 232 | 67.6 | 554 | 1,035 | 15.6 | 100% |
| | 8 | 1.62 | 414 | 66.6 | 705 | 2,028 | 19.0 | 96% |
| | 12 | 1.74 | 444 | 47.1 | 1,497 | 3,116 | 23.8 | 92% |
| | 16 | 1.96 | 502 | 38.9 | 1,575 | 4,162 | 28.6 | 90% |
| | 24 | 2.21 | 565 | 27.6 | 1,598 | 6,280 | 39.1 | 88% |
| Long, 3,072 in / 512 out | 1 | 0.13 | 69 | 76.7 | 790 | 791 | 13.1 | 100% |
| | 4 | 0.41 | 212 | 63.2 | 1,001 | 2,872 | 18.7 | 81% |
| | 8 | 0.69 | 354 | 59.2 | 1,521 | 5,266 | 24.3 | 78% |
| | 12 | 0.69 | 353 | 33.2 | 1,974 | 9,101 | 31.6 | 77% |

## Reading the results

Chat throughput scales almost linearly to 8 concurrent requests: 5.7 times the
single-stream rate, with each stream still decoding at 67 tokens per second. Beyond 8, the
GPU adds little. Twelve streams gain 7% throughput, but each slows by 30% and the slowest
first tokens take 3 s. Long prompts saturate earlier, and throughput stops rising after 8.
At 4, the slowest first token already takes 2.9 s, because prefilling 3,072-token prompts
competes with decoding.

Advertise **8** if traffic resembles the chat profile, or **4** if long prompts are common
and a 2-second first token matters. Set `--max-num-seqs` to at least the advertised
capacity. The current bundle advertises 1 against `--max-num-seqs 4`, which leaves most of
the GPU idle. A single advertised slot also makes the cached-capacity refusals in the
[repin run](m3-upstream-2026-10-29.md) far more frequent.

## Limits

Random-token prompts disable prefix-cache reuse, and forced output lengths remove
early stops. Real traffic will differ in both directions. Each level ran once. The node,
payment and gateway add per-request overhead that this direct test does not measure. After
changing capacity, re-render the bundle, approve the new deployment revision, and run a
short paid check at the chosen concurrency through the gateway.
