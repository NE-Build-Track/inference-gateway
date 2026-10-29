# Upstream repin and image publication

Publish the next gateway, node and payment-stack images built on current dependencies:
Builder Core from NE-Build-Track `releases/m3`, and Node, Clearinghouse Batteries and the
Python SDK from `main` in the `livepeer/` organization. Publication goes to NE-Build-Track
infrastructure only; nothing is pushed to upstream Livepeer repositories.

## Acceptance

- The full suite passes on the repinned lockfile; native startup accepts the installed Core.
- Paid failover follows the documented policy: on, at most `LPB_MAX_PAID_ATTEMPTS` (2).
- The built gateway image contains the pinned Core and SDK; node and Clearinghouse binaries
  report their source commits.
- Paid native vLLM on the remote GPU host: Chat, Responses and Messages in JSON and SSE, image input,
  preserved backend errors, disconnects, overlap, ten sequential calls and runtime restart,
  with exact retail charges and wholesale reconciliation.
- Repository live suites run against the candidate; any failure is explained, not loosened.

## Work

- [x] Repin Core to `d3df806`; the lock now resolves SDK `livepeer/…@2017a80`.
- [x] Build Node `a4b68ed` and Batteries `d47d1ff` from `livepeer/`, passing `COMMIT`.
- [x] Keep paid failover on (`LPB_MAX_PAID_ATTEMPTS=2`, decided on `main`); an interim
      forced cap of 1 was dropped when merging `main`. Live paid failover across orchestrators
      remains unexercised: the acceptance topology had one orchestrator.
- [x] `make check` and gateway image smoke.
- [x] Forwarded native errors carry `x-should-retry: false` (found during review; SDK contract tests).
- [x] Paid remote GPU acceptance and cleanup; see the [evidence](../../../live-runner/tests/m3-upstream-2026-10-29.md).
- [ ] Commit, review and merge the gateway branch.
- [x] Default ticket expected value 2e10 wei; signer caps tickets at 1e11 wei and fixed prices
      at $0.01. Paid checks served at 2.9% overpayment and refused coarse or over-priced terms
      before payment.
- [x] Compose image names match the registry repositories; `IMAGE_REGISTRY` and `IMAGE_TAG`
      select a published release.
- [ ] Push three images under `m3-upstream-20261029` to `registry.livepeer.tools/ne-build-track/`:
      `inference-gateway` (local `inference-gateway:local`), `inference-node` (signer,
      orchestrator and runner bundle; local `inference-node:a4b68ed`) and
      `inference-clearinghouse` (local `inference-clearinghouse:d47d1ff`). Each local image ID
      becomes the published config digest; record the manifest digests. Leave `v0.0.1` unchanged.
- [ ] Update `live-runner/images.lock.json` with the published node digest; regenerate
      bundles and issue approvals for the new deployment revision. Then update the node-lock
      statements in `docs/product-specs/native-inference.md` and `live-runner/contract/README.md`.
- [x] On-chain ticket terms are configurable; rendering rejects a face value below the capped
      redemption gas cost (the previous fixed values cost 9.6 times the ticket to redeem).
- [ ] Pin the example orchestrator's vLLM image by digest, or mark it untested.
- [x] Advertise capacity 8 in the supported vLLM manifest; the renderer sets `--max-num-seqs`
      from capacity. A paid check passed at 8 concurrent streams; see the
      [capacity benchmark](../../../live-runner/tests/vllm-capacity-2026-10-29.md).
- [ ] Observe finality for the 29 October winning tickets held by the private paid node.

## Verification

`make check`: 532 passed, plus lint, formatting, strict typing and documentation checks.
`uv run python scripts/smoke-image.py` passed. Paid acceptance: every probe case passed. The
provider suite passed 10 of 12; the two failures are forced-tool semantics. The accounting
suite passed with forced-tool cases skipped. Remaining limitations are in the
[debt tracker](../tech-debt-tracker.md).
