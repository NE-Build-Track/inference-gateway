# Related repository compatibility — 28 October 2026

> **Resolved later the same day.** Builder Core PR #55 made the port this review requires and
> merged into `releases/m3` as `2134e29`. Core's own paid call now carries raw bodies, protocol
> headers, response headers and returned HTTP errors (Core D56), and the SDK is on upstream
> `2017a80`. The gateway now pins the later `releases/m3` commit `d3df806`, which includes it;
> see the [upstream repin evidence](../../live-runner/tests/m3-upstream-2026-10-29.md). Paid
> failover is kept rather than turned off with `LPB_MAX_PAID_ATTEMPTS=1` (item 4 below): under
> `return_http_errors`, Core fails over a 503, 404 or 409 only while another runner can be
> tried, and returns the answer it stops at unchanged, which settles the replay concern in
> item 3. The review below is unchanged.

The locked gateway remains the supported combination. Latest Builder Core M3 cannot yet
replace it: an isolated run against `efb9c03f9eb4ed5b90cd4ee391c71025f62c126d` passed
473 tests and failed 27 native transport tests. This supersedes the previous review's
latest-M3 overlay result; that result applied to `0e2eb6a`, before the paid-call rewrite.

## Revisions reviewed

| Repository | Fetched NE branch | Gateway disposition |
| --- | --- | --- |
| Builder Core | `releases/m3` at `efb9c03` | Retain native `0d0b532`; rework PR #55 before upgrading |
| Python Gateway SDK | `main` at `2017a80`; M3 at `13edf39` | Retain Core's transitive native pin `cd6ed5e` for the current transport |
| Node | `main` and `releases/m3` at `dfcf033` | Already the native bundle's source revision; no node patch needed |
| Clearinghouse Batteries | `main` at `4b32940`; M3 at `874716b` | No new default/release commits since the previous review; retain existing deployment pins |
| Runner examples | `main` at `a526b43` | Unchanged; gateway-owned live-runner bundle remains authoritative |

Core main is still M2 (`eb3c3ba`). Reviews and future ports must target `releases/m3`.
Core PR #55 and SDK PR #6 remain open. No sibling checkout was edited. No registry tags,
GPU services or paid development services changed during this review.

## What changed in Core

Core PR #61 (`ea5013c`) moves the paid runner call into Core's
`adapters/runner_call.py`. It records signer intent before signing, retains uncertainty
when a signer may have charged, and distinguishes safe orchestrator failover from work
that may already have run. Core also separates total wholesale `JobCost.fees` from
`JobCost.billable`, the delivered attempts' subset.

PR #65 (`6a7b734`) adds `LPB_MAX_PAID_ATTEMPTS` (default 2). Setting it to 1 disables
paid failover while still allowing unpaid candidate failures. This is distinct from the
caller's `max_attempts` bound. JWT closeout and the copied SDK's license notice also landed.

The new paid transport does not use SDK `call_runner`, so merging SDK PR #6 alone will
not enable native inference on latest M3. Its `JobRequest` still lacks `raw_body`,
`content_type`, protocol `headers`, and `return_http_errors`; its stream facade lacks
response headers. Its transport builds JSON from `payload`. Re-encoding the gateway's
requests would violate the native contract and still omit response/error behavior.

## Gateway updates and retained behavior

Native configuration now checks the installed Core request fields and response-header
facade before constructing an engine or opening resources. An incompatible install fails
startup with a fixed diagnostic recommending `uv sync --locked`. Validated-only routes do
not require the native contract. This catches an incompatible install early; it does not
certify arbitrary versions or replace transport acceptance tests.

The wholesale adapter correctly reads total `cost.fees`, retaining failed-attempt costs
and classifications. Do not replace that with `billable`: gateway retail charges follow
its own usage/rate contract, and wholesale reports must include operator expenses.
Core JWT configuration does not change gateway API-key authentication; the gateway embeds
Core with an explicit actor. Public offerings changes do not affect candidate selection.
Metadata-based runner identity and independent operator approvals remain intact.

## Required Core PR #55 update

Port onto current M3, preserving the new paid transport and evidence lifecycle:

1. Add bounded raw-body/content-type/header fields and include them in operation identity.
   Preserve old parsed-JSON callers. Reject payment/auth header injection.
2. Extend Core's own `PaidRequest` to send the original bytes, forward allowed protocol
   headers and expose safe response headers. Disable native decompression, redirects and
   implicit transport retries. Keep 402 challenges as payment control flow.
3. Deliver opted-in non-402 HTTP errors without losing their bytes or status, recording
   completed error jobs as failed. Resolve the interaction with Core's 404/409 failover:
   native error forwarding must not silently replay a backend response on another runner.
4. Preserve signer-intent records, `payment_unknown`, cancellation/deadline finalization,
   and the paid-attempt cap. Before the gateway upgrade, use `LPB_MAX_PAID_ATTEMPTS=1`
   to preserve its existing no-paid-failover behavior. This setting is not available in
   the currently pinned Core and is not presented as active gateway configuration.
5. Test original bodies, image inputs, Anthropic headers, SSE, error bodies and usage,
   plus signer uncertainty, paid/unpaid failures, disconnects and accounting classifications.
   Run Core's full suite and the gateway suite on the exact resulting revision.
6. Repin the gateway and lockfile only after that passes. The SDK patch may no longer be
   needed on the new path; remove it only after equivalent native transport coverage passes.
   Build the resulting image and repeat combined GPU/payment acceptance before promotion.

This work belongs in the NE Core fork. A gateway-owned HTTP client or old SDK transport
substitution would bypass Core's newly accepted payment protections.

## Verification

- Latest M3 isolated environment, before the startup guard: **473 passed, 27 failed**.
  Failures cover native request construction, SDK clients, errors, streaming and images;
  there were no failures outside native tests.
- Locked dependencies: full `make check` rerun after the guard (see quality status).
- The guard tests cover each missing request field, missing response headers, and valid
  startup configuration for both route modes without constructing a real payment engine.
- No live GPU, payment, settlement or production load claims are made by this review.
