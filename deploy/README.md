# Gateway deployment

Adapted from the local ChatLPT checkout at commit `8a6a8af` on 2026-10-25. The gateway
now embeds Builder Core when `GATEWAY_BACKEND=livepeer`; Compose selects that mode by
default. Local Python startup defaults to `fake` unless configured otherwise.

## Copied configuration

The payment Compose services, pinned upstream images, signer/Clearinghouse TOML files,
secret initialization, allocation bootstrap, and certificate pinning scripts came from
ChatLPT. The Dockerfile now builds only the Python gateway, without ChatLPT's frontend.

`deploy/.env` was copied unchanged locally, mode 0600 and ignored by Git. Its payment
credentials and existing allocation are reused. The copied secrets and original certificate
pins also remain private local files, outside Git and Docker build contexts. No database
or Docker volume was copied. `.env.example` documents gateway settings with placeholders.

Compose overrides the engine ID/database location and maps `CHATLPT_CHAT_APP` and
`CHATLPT_APPROVED_ORCHESTRATORS` into gateway settings as fallbacks. Explicit
`GATEWAY_CHAT_APP` and `GATEWAY_APPROVED_ORCHESTRATORS` take precedence. Other `CHATLPT_*`
values do not configure the gateway. See [Livepeer configuration](../docs/design-docs/livepeer.md)
for the supported model routes, identities, limits, and payment prerequisites.

## Build and validate

From the repository root:

```sh
uv sync --locked
uv run python scripts/prepare-runner-trust.py
scripts/build-images.sh gateway
docker compose -f deploy/compose.yaml config --quiet
docker compose -f deploy/compose.yaml --profile payment-stack config --quiet
uv run python scripts/smoke-image.py
docker run --rm --network none --read-only --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges:true -i endpoint-gateway/gateway:local \
  python - < scripts/smoke-livepeer.py
```

The first smoke checks the fake-backed HTTP server. The second exercises the real Builder
Core lifecycle with a scripted transport, fake credentials and networking disabled.

The trust-preparation script reads existing public pins, creates a readable OpenSSL hash
directory at `deploy/config/runner-trust`, and performs no network fetches. The gateway
mounts this directory alongside the system CA trust store. The source pins and payer
secrets retain their restricted permissions. Run preparation after intentionally updating
pins with the separate trust-on-first-use pinning script. Review those trust changes.

Use `config --quiet`: expanded Compose output contains the private env-file values.
The image smoke runs a temporary fake-backed container, checks readiness and both API
protocols, then removes it and its anonymous volume. It mounts no credentials and does
not join the payment network.

## Reuse the existing ChatLPT payment stack

The copied allocation key belongs to the source Clearinghouse database. The existing
payment stack is therefore the simplest way to use it without migrating state or creating
another allocation. The network overlay connects only this gateway to that stack:

```sh
docker compose -f deploy/compose.yaml -f deploy/compose.chatlpt.yaml config --quiet
# After confirming the existing stack is the intended payment source:
docker compose -f deploy/compose.yaml -f deploy/compose.chatlpt.yaml up -d gateway
```

The external network defaults to `chatlpt_default`; set `CHATLPT_NETWORK` if the source
stack uses another name. The overlay points to its `clearinghouse` service for signer
port 8937 and management port 8081. It creates neither payment service. Do not enable the
`payment-stack` profile with this overlay: the copied payer account must have one signer.
The source stack must already be running; readiness stays unavailable if discovery is stale
or a configured app has no candidates. Readiness does not guarantee sufficient allowance.

The listener defaults to `127.0.0.1:8000`; change `GATEWAY_PORT` if needed. Set
`GATEWAY_API_KEY` for shared-key authentication. This is one application actor/allocation;
per-user identity, tenant isolation, and retail billing are not implemented.

## Run offline

```sh
GATEWAY_BACKEND=fake docker compose -f deploy/compose.yaml up -d gateway
```

This starts no signer or Clearinghouse. Any model name uses the deterministic fake.
Use `fake-model` in examples; use the configured public route name (initially `qwen3.8-27b`)
with the Livepeer backend. Set `GATEWAY_BACKEND` explicitly when switching modes.

## Separate payment stack

The copied services remain pinned:

| Component | Commit |
| --- | --- |
| Clearinghouse Batteries | `ca09e47c067682fb48c3f15425546fb6aa5b6659` |
| Node signer | `406b248f6cb8728e6e7eba1809901f589060bc6b` |

They are behind the `payment-stack` profile. The signer shares Clearinghouse's network
namespace; webhook and Kafka remain on loopback. A separate deployment uses this project's
`paid-state` volume, which does not contain ChatLPT's allocation records. Copying credentials
alone does not make a fresh Clearinghouse recognize the old allocation.

Provision a separate stack only with a deliberate payer/state plan. `first-run.sh AMOUNT_USD`
creates a funded grant/allocation, requires an explicit amount, and refuses to overwrite an
existing key. `init-secrets.py` refuses a populated directory. Neither script was run during
implementation. Starting two signers with the copied payer is not a migration strategy.

## Persistent state and shutdown

The `gateway-data` volume stores the embedded engine database. Engine migrations run on
startup; retain and back up this volume. Do not share it between gateway processes. Shutdown
lets Builder Core finalize its active jobs before closing its store. The gateway itself does
not save conversation history, replies, or a duplicate cost ledger.

`/healthz` checks process liveness. `/readyz` checks configured backend readiness and is used
by Compose. A live paid smoke test has not been run; offline tests and builds do not verify
network availability, the allocation balance, or current model behavior.
