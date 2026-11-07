# Runner contract v1

The [live runner standard](https://github.com/NE-Build-Track/live-runner-standard) replaces the
runner contract this repository defined. Runners now publish a `runner_contract: 1` envelope under an
App ID that names only a capability, and each wire profile's behavior lives in a registry entry. The
gateway and the bundled runners move to it together, with no compatibility for the old
`inference_contract` metadata: a v1 gateway ignores those runners, as the standard requires.

## Acceptance

Gateway:

- Reads only `runner_contract: 1` envelopes through `livepeer-runner-contract` 0.3.0, against registry
  release `2026.11.3`, whose entry digests are checked at load.
- Ignores every other runner, one at a time, and logs each once with the reason and a link to the
  standard. A malformed, oversized or deeply nested record never fails a discovery refresh.
- Routes name a capability (`chat`, `decision`), not an operator's App ID, and select by capability,
  model name and wire profile.
- Validated routes accept `candidate` runners without approval; a `private` runner, or one with an
  admission value the gateway doesn't know, needs an approval. Native routes need an approval for
  every runner.
- Approvals key on orchestrator origin, runner ID, `deployment_revision`, profile and model, expire,
  and each carries a passing conformance report for the same runner, revision and profile. They are
  read from a file that the gateway reloads when it changes, with no restart.
- Native requests are checked against the entry's request schema before payment. A failure is a 400.
- A native answer succeeds only when the entry's success rule holds and no error mapping matches.
- Runners whose envelope overrides an entry's path or method are not selected: Builder Core sends one
  path per job.

Runner bundle:

- The renderer emits, per registration, a standard `runner.toml` manifest, the envelope and the
  node's `[[Runners]]` block. `runner-contract check` passes on the block, and the library recomputes
  the envelope's revision from the manifest.
- The revision follows the standard's recipe: runtime and readiness images, model source and
  revision, profiles and behavior settings. URL, capacity, price and tool code stay out.
- A deployment that advertises chat profiles and `systemone` renders two registrations, `chat` and
  `decision`, sharing a `capacity_group`.
- Readiness answers 200 with `{"status":"ready"}` only after a self-test of every advertised profile
  passes, and 503 with `starting` or `failed` otherwise.
- The manifest requires a price.

## Decisions

- **The standard's library, not our own parser.** `parse_envelope`, `select`, `validate_request` and
  `ResultReader` implement the gateway rules; the gateway adds only its policy (capacity, approvals,
  route configuration). Both packages are pinned by commit, as Builder Core is.
- **The contract lives in `backends/`.** Architecture tests keep `livepeer_*` imports behind the
  backends and the composition root, so `app.py` reaches schema checks through the native port.
- **Approval evidence.** The user chose to require a passing conformance report with each approval.
  Reports aren't signed (the standard's interim rule), so a report is evidence the operator chose to
  trust, not proof.
- **`Status = "ready"` in rendered blocks.** The library writes it, and an honest self-test makes it
  true. The bundle's node stays offchain and binds loopback, so a rendered candidate still serves no
  network traffic until an operator moves it.
- **`runner_id` stays in the envelope.** The standard allows it when the node doesn't report it, and
  discovery identity still depends on it.
- **The v2 chat profiles.** Registry `2026.11.3` adds `openai.chat.v2`, `anthropic.messages.v2` and
  `openai.responses.stateless.v2`, and the gateway speaks those. Their request schemas pass the tool
  and item types coding agents send, and Responses v2 counts an `incomplete` answer as success, so
  the gateway's own exception for a capped Responses answer is gone. The request check runs in a
  worker thread, and a native refusal names the problem without quoting the request.
- **System One follows the entry.** Its request schema allows no other top-level fields, so
  `stream: false` and misspelled optional fields now fail before payment. The TypeSafe SDK 0.7.5
  sends none of them.

## Steps

- [x] Pin `livepeer-runner-contract` and `livepeer-runner-registry`.
- [x] Gateway contract module in `backends/`: registry, envelope cache, selection, request checks.
- [x] Discovery filter and Core selection hook on the contract; ignored-runner log.
- [x] Approvals file with conformance reports and reload.
- [x] Routes by capability; remove `GATEWAY_CHAT_APP`, `GATEWAY_UPSTREAM_MODEL` and
  `GATEWAY_NATIVE_APPROVALS`.
- [x] Native port: request check and profile success.
- [x] Bundle: manifest, envelope, block, revision, split registrations, readiness self-test.
- [x] Deploy stack's vLLM runner: v1 envelope, digest-pinned images, model revision, readiness.
- [x] Tests, documentation and the debt tracker.
- [x] `make check` (700 tests) and `uv build`.
- [ ] Live cutover: the qwen registration on its node and the gateway deploy, in one window.
- [ ] Conformance reports from the live runners, and approvals carrying them.

## Verification

```sh
make check
uv build
uv run python live-runner/tooling/render.py live-runner/local/deployment.json \
  --output live-runner/generated/vllm
uv run runner-contract check live-runner/generated/vllm/<id>.block.toml \
  --manifest live-runner/generated/vllm/<id>.runner.toml
```

## Unresolved

- Rollout: the qwen runner and the deployed gateway change in one window, coordinated with the
  standard team's node edit.
- Builder Core keys concurrency limits, offerings and job records on App ID, so every chat model now
  shares `chat` there. Core doesn't fail over on 429, which the entries map to `busy`.
- Conformance reports against the live runners are produced on their hosts, outside this repository.
- A passing report for the live qwen runner: `conform` 0.3.0 leaves reasoning models room in its
  success cases, and the bundle now declares `reasoning: on`. Native qwen routes wait on that
  report. The bundle's own `readiness.py` should give way to the standard's `runner-contract ready`,
  once its container has a pinned way to install the tool.
