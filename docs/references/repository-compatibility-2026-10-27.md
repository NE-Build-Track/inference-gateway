# Related repository compatibility — 27 October 2026

> **Superseded** by the [28 October review](repository-compatibility-2026-10-28.md), and that by
> Builder Core `2134e29` (PR #55 merged), which the gateway now pins.

The gateway's pinned dependency set passes. A blind move to the latest default/release
branches does not preserve native inference. The review fixed a gateway deployment gap;
no dependency pins or registry image tags were changed. The dev stack stayed stopped.

## Findings and disposition

| Component | Compared revisions | Result |
| --- | --- | --- |
| Gateway | Native branch at `96364a2`, then this discovery fix | Baseline 481 tests; 483 after the fix. Its `main` (`bb76383`) still predates native support. |
| Builder Core | Pinned `0d0b532`; latest `releases/m3` `0e2eb6a` | Plain latest M3 fails 20 of 61 selected native tests because native PR #55 is absent. An isolated latest-M3 overlay with commits `118e4d1`, `4a9747f`, `0d0b532` passes all 483 gateway tests and strict typing. |
| Python SDK | Pinned `cd6ed5e`; NE `main` `2017a80` | Main lacks the native protocol-header/error-stream patch. Keep the native pin until its PR is accepted. |
| Node | Bundle `e75e5b6`; NE main/M3 `dfcf033` | Main lacks explicit runner identity and signer propagation. Keep the corrected candidate pin. No newer node behavior was substituted. |
| Clearinghouse Batteries | Legacy Compose pin `ca09e47`; NE main `4b32940`; local main `8bf0d5e` | Newer changes include releases/dependencies and Kafka advertised-address configuration. Auth/service-auth/application package tests pass on local `8bf0d5e`; source review found no gateway API migration requirement. This is not a full paid deployment certification of that version. |
| Runner examples | Main `a526b43` | No newer default-branch change in the fetched checkout. Examples are reference material; the gateway-owned live-runner bundle remains the integration contract. |

Core `main` is the older M2 line (`eb3c3ba`); M3 work belongs on `releases/m3`.
Clearinghouse local main is ahead of the fetched NE main; those are not interchangeable
release claims. SDK/node native branches are ahead of their defaults, not obsolete pins.

## Gateway-owned fix

The Compose stack and example environment omitted `LPB_DISCOVERY_URL`, while the pinned
Core gets runner inventory from its separate discovery service. Keeping orchestrators in
the signer's configuration did not supply that inventory to Core. This could leave a
fully configured payment stack without usable runners.

The gateway now rejects missing discovery configuration before constructing the engine.
Compose adds `lpb discovery` using the same gateway image and therefore exactly the same
Core revision. It reads `config/orchestrators.txt` and shares the gateway's runner TLS trust.
The signer configuration only signs payments. Operator instructions start discovery alongside
the gateway; an externally managed inventory can be selected through `LPB_DISCOVERY_URL`.
The certificate-pinning helper now reads the discovery directory instead of signer config.

A regression test executes the actual discovery CLI against an isolated loopback orchestrator
and asserts runner ID, proxy-host URL and metadata preservation. No GPU, funded signer or
external orchestrator is involved. A second test rejects missing discovery configuration
without invoking the engine factory, then verifies forwarding the configured URL.

## What the newer Core changes mean

M3 adds JWT bearer configuration and makes offerings public (`offerings()` no longer takes
an actor). Gateway catalog selection uses `discovery.candidates(app)`, so the offerings
signature change does not require a gateway adapter change. The gateway's own API-key and
retail identity system is not automatically replaced by Core's HTTP JWT mode.

The isolated native overlay needed two pyproject conflict resolutions: retain
`pyjwt[crypto]>=2.13` from M3 and keep the native SDK pin (ultimately `cd6ed5e`). Code changes
otherwise applied. This overlay is a test combination, not a published or merged Core commit.
Core's shared checkout had staged documentation changes; those were left untouched.

## Verification and remaining release work

- Current pinned `make check`: 483 tests, lint, formatting, strict typing and documentation.
- Latest M3 plus native patches: 483 gateway tests and gateway strict typing pass.
- Latest M3 without the patches: 20 native tests fail, 41 pass; not an upgrade candidate.
- Newer local Clearinghouse: `go test ./internal/auth ./internal/serviceauth ./internal/app` passes.
- Compose payment-stack configuration validates without rendering secrets to output.
- Local gateway image `ne-build-track/inference-gateway:compat-review` builds; its packaged
  `lpb discovery --help` works with networking disabled. No registry tag was moved.

Before repinning, review/merge the native Core/SDK/node PRs and test the exact resulting
artifacts. The older `deploy/livepeer/orchestrator` recipe and payment images are separate
legacy pins; use the native `live-runner` bundle for native deployment. Upgrading legacy
node pins requires reviewing CLI/config formats (including TOML price types), not merely
replacing the tag. No live GPU, full payment stack or on-chain finality was retested here.
The Compose/runtime fix requires a newly built gateway image when deployed; existing
published images have not changed.
