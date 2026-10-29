# Release candidate paid check — 29 October 2026

This run checks the gateway image that would be published, built from `main` after PR #6:
`inference-gateway:local`, `sha256:fd175fb0…`. It repeats the paid native matrix and the
capacity check, and runs live paid failover between two orchestrators for the first time.
Every check passed.

The image ID matched an earlier build from the same commit, so the build reproduces. That
earlier build had been removed by an unrelated image prune on the build host before this run.

## Topology

The supported manifest, [qwen3.8-27b-awq.json](../runtimes/vllm/qwen3.8-27b-awq.json), was
rendered with capacity 8, and vLLM ran the rendered command line on a remote RTX 5090. The
payer, signer, Clearinghouse and paid orchestrator identity match the
[repin run](m3-upstream-2026-10-29.md), and the tickets used the repository defaults.

Unlike earlier runs, the gateway took its inventory from Core's discovery service
(`lpb discovery` from the same image), probing every 5 seconds instead of the default 30.
Two paid nodes ran on separate data volumes:

- **B** served the vLLM runner.
- **A** advertised the same runner, but its runtime answered every generation with 503.

Paid failover followed the documented policy: `GATEWAY_MAX_ATTEMPTS=3` and
`LPB_MAX_PAID_ATTEMPTS=2`.

## Results

| Check | Result |
| --- | --- |
| Chat, Responses and Messages, JSON and SSE; image input | 7 of 7 served; one paid attempt and one exact retail charge each |
| Backend 400; chat stream without usage | 400 preserved with no charge; missing usage rejected before dispatch |
| Second request while a stream runs | Served (capacity 8) |
| Mid-stream disconnect | Abandoned with no charge; the next request was served at once |
| 3 rounds × 8 concurrent paid streams | 24 of 24 served, one paid attempt each; first token within 1.8 s |
| Overflow burst of 10 (B only in discovery) | 8 served; 2 paid refusals with `x-should-retry: false`, no failover available |
| Paid failover (A and B in discovery), 20 requests | 20 of 20 served; one went to A first and failed over to B |

The failover request was paid at A, refused with 503, then paid and served at B. Core recorded
attempt 1 as `failed_after_payment` and attempt 2 as `delivered`, for $0.002 of wholesale
cost. Retail charged the client once, for the measured usage. The other 19 requests went
straight to B. Core's default ordering puts the runner with the most free capacity first and
breaks ties by discovery order, so A came first only when B's last sample showed less capacity.

The run made 67 paid attempts across 66 requests. Their $0.067 of observed wholesale cost
matched the allocation's spend exactly. The nodes accepted 1,430 tickets and none won,
consistent with a one-in-20,000 probability.

All task-owned containers were removed afterwards. The remote host kept its existing services
and volumes, and GPU memory returned to idle.
