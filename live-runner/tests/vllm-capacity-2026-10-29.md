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

## Paid check at capacity 8

The supported manifest, [qwen3.8-27b-awq.json](../runtimes/vllm/qwen3.8-27b-awq.json), was
rendered with capacity 8. The renderer passed `--max-num-seqs 8`, and vLLM ran that exact
command line. The node, payment stack, payer and tunnelled split-host topology matched the
[repin run](m3-upstream-2026-10-29.md). The gateway image was rebuilt from commit `a316afc`
(`sha256:526ccefc…`). The paid node used a new data volume and the repository's ticket
defaults: a 0.0004 ETH face value, a one-in-1000 win probability and a 1 gwei redemption
fee cap. The node accepted that configuration.

Discovery advertised 8 available slots. Three rounds of 8 concurrent paid streams then ran,
each after a discovery refresh. All 24 returned 200. Each made one paid attempt and received
one exact retail charge; no request had a second attempt. First tokens arrived in 1.7–2.9 s
(median 2.2 s), and streams completed within 4.5 s.

An overflow burst of 10 sent all ten to the runner, because the fresh sample showed 8 free
slots. Eight were served. Two were paid and refused with 503, and both carried
`x-should-retry: false`, so default SDKs would not repeat them.

All 34 signed payments reconciled: $0.034 of observed wholesale cost, matching the
allocation's spend exactly. The node received 32 payments and charged 32 requests. The two
refused requests were signed but never received. It accepted 64 tickets and none won, as
expected at that probability.

## Findings from the paid check

**Gateway first-token latency.** The median first token took 2.2 s through the gateway with
19-token prompts. The direct benchmark at the same concurrency took 0.7 s with 1,024-token
prompts, so the overhead is at least 1.5 s. It covers the payment challenge, signing and
authorization, the node, and the tunnel. This run does not separate those parts.

**Ticket granularity.** Each request needed two tickets. The default expected value per
ticket (4e11 wei) sits just below the $0.001 price, about 4.26e11 wei at the current ETH
rate. Two tickets carry about 1.9 times the price in expected value. A smaller expected value
per ticket, or one just above the price, would reduce that. The surplus is lost to the payer:
the node charges a fixed-price session once, and each request opens its own session.

## Ticket terms and signer limits

The orchestrator default expected value per ticket is now 2e10 wei, and the gateway's signer
caps it at 1e11 wei (`MaxTicketEV`) and a fixed price at $0.01 (`MaxFixedPrice`). Three paid
cases checked these limits through the gateway. Each used the same payer, signer and paid
node, against a minimal local runtime with no model. Payment happens before the runtime is
reached, so the runtime does not affect the result.

| Case | Orchestrator terms | Result |
| --- | --- | --- |
| New defaults | 2e10 wei per ticket, $0.001 per request | 5 of 5 served; 22 tickets per request, 2.9% over the price; one charge each |
| Coarse tickets | 4e11 wei per ticket, above the signer cap | Both refused with 503 before signing; `payment_refused`; nothing paid or charged |
| Over-priced runner | $0.02 per request, above the signer cap | Both refused with 503 (signer status 481); nothing paid or charged |

Refusals carried `x-should-retry: false`. Nothing was paid, so a gateway with more than one
allowed attempt can try another orchestrator. The five served requests reconciled exactly:
$0.005 of observed wholesale cost, matching the allocation's spend. None of the 110 tickets
won, as expected at one in 20,000.
