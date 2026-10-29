# Livepeer deployment

This directory contains two independent Docker Compose projects. Neither requires another
application checkout. Build contexts use this repository or pinned upstream commits.

| Project | Components | Persistent state |
| --- | --- | --- |
| `gateway/compose.yaml` | Gateway, discovery, optional Clearinghouse and payment signer | User/retail DB, engine DB, payment DB and signer state |
| `orchestrator/compose.yaml` | `livepeer/node` orchestrator and local vLLM | Model cache and orchestrator payment state |

## Gateway configuration and build

From the repository root:

```sh
cp deploy/livepeer/gateway/.env.example deploy/livepeer/gateway/.env
chmod 600 deploy/livepeer/gateway/.env
scripts/build-images.sh
```

To use published images instead of building, set `IMAGE_REGISTRY` and `IMAGE_TAG` in `.env`,
then `docker compose -f deploy/livepeer/gateway/compose.yaml pull` and start services with
`up --no-build`. Image names match the registry repositories: `inference-gateway`,
`inference-node` (signer and orchestrator) and `inference-clearinghouse`. A single release
tag covers all three; `--build` would overwrite that tag with a local build.

Edit `.env` with your routing and payment settings. `GATEWAY_ACCOUNTING_DB=/data/gateway.db`
enables registry authentication. Leave `GATEWAY_API_KEY` unset in this mode. The gateway uses
one upstream application allocation; users and their retail charges remain separate.

For a new payment installation, run `python3 deploy/livepeer/gateway/init-secrets.py` once,
then provide the payer keystore, exact-byte password and RPC URL files described by that
script. It refuses to overwrite a populated secrets directory. Set the engine token and
Fernet secret in `.env`, along with the allocation settings. Keep all generated files private.

```sh
docker compose -f deploy/livepeer/gateway/compose.yaml --profile payment-stack up -d clearinghouse signer
# Only for a NEW allocation: explicitly choose the funding amount.
deploy/livepeer/gateway/first-run.sh AMOUNT_USD
docker compose -f deploy/livepeer/gateway/compose.yaml up -d discovery gateway
```

The first-run script creates a grant/allocation and refuses to replace an existing allocation
key. It does not fund the payer's on-chain deposit/reserve. Those require separate operator
actions. Never run two signers using the same payer/state. To use independently managed
payment services, omit the profile and set their reachable `LPB_SIGNER_URL` and
`LPB_BATTERIES_URL` instead. No external Docker network is required by the manifests.

The committed `gateway/config/orchestrators.txt` contains example public orchestrator
origins. Replace those and `GATEWAY_APPROVED_ORCHESTRATORS` together for your deployment.
The separate `lpb discovery` service reads that directory; the signer only signs payments.
`LPB_DISCOVERY_URL` defaults to `http://discovery:8939/discovery` in Compose and is required
for direct gateway startup. Set it explicitly when using an external discovery service.
Discovery and gateway use the same image/Core revision and TLS trust directory. Do not bypass
TLS verification: public certificates use system trust; intentionally pinned certificates can
be placed in `gateway/config/pinned-certs` and processed with:

```sh
uv run python scripts/prepare-runner-trust.py
```

The generated `gateway/config/runner-trust` directory is mounted alongside system roots.
Create an empty directory there if using system trust only. Expanded Compose output contains
secret values; validate with `docker compose -f deploy/livepeer/gateway/compose.yaml config --quiet`.

## Users, keys and retail prices

Run the CLI in the gateway container so it uses the deployed database:

```sh
docker compose -f deploy/livepeer/gateway/compose.yaml exec gateway gateway users create alice
docker compose -f deploy/livepeer/gateway/compose.yaml exec gateway gateway keys create USER_ID \
  --name development --scopes inference,reports
```

Record the issued secret in your credential store. Use `admin` for a dedicated administrative
key, and `inference` for clients that should not read reports. Set rates deliberately before
serving a model; this example is configuration, not an upstream price quote:

```sh
docker compose -f deploy/livepeer/gateway/compose.yaml exec gateway gateway prices set \
  --model qwen3.8-27b --version qwen-v1 --currency usd \
  --input-per-million 0.5 --cached-input-per-million 0.1 --output-per-million 3
```

Use a new version for price changes. Other CLI commands include `users list`, `users disable`,
`keys list`, `keys revoke`, `keys rotate`, `prices list`, `adjust` and `accounting retry`.
Run `gateway COMMAND --help` for arguments. See [accounting](../../docs/design-docs/accounting.md)
for authorization, reporting endpoints, exact monetary semantics and failure recovery.

## Local vLLM orchestrator

Requirements: Docker Compose with GPU device reservations, an NVIDIA GPU with enough VRAM,
NVIDIA Container Toolkit, model-download access and disk space. The default small Qwen model
is a development example. Changing the model may require a different tool parser or image.

```sh
cp deploy/livepeer/orchestrator/.env.example deploy/livepeer/orchestrator/.env
python3 deploy/livepeer/orchestrator/render-config.py --mode offchain
docker compose -f deploy/livepeer/orchestrator/compose.yaml up -d --build
```

The node statically registers the local vLLM HTTP server, checks its health and advertises
`RUNNER_APP`. vLLM is not exposed directly on the host. The orchestrator listens on host
loopback by default. Configure `ORCHESTRATOR_BIND` and `ORCHESTRATOR_SERVICE_URL` for remote
access, with a TLS reverse proxy for public deployment. The advertised address must be
reachable from gateway/discovery containers; their `localhost` is not the host machine.

For gateway routing to this runner, set `GATEWAY_CHAT_APP=inference/chat/default`, the desired
public `GATEWAY_MODEL_NAME`, and `GATEWAY_UPSTREAM_MODEL=gateway-model`. Configure discovery
origins to reach this orchestrator. The existing gateway payment backend
still requires its normal engine/signer configuration even when the runner is free.

### Paid on-chain configuration

Provide `orchestrator/secrets/keystore.json`, `keystore-password` and `rpc-url` with owner-only
permissions. Set an active registered `ORCHESTRATOR_ADDRESS`, the public service URL, and
wholesale `RUNNER_PRICE_USD`/`RUNNER_PRICE_UNIT` in the orchestrator `.env`. Then:

```sh
python3 deploy/livepeer/orchestrator/render-config.py --mode onchain
docker compose -f deploy/livepeer/orchestrator/compose.yaml up -d --build
```

The generated configuration selects Arbitrum One and its ETH/USD feed. Ticket terms come from
`TICKET_FACE_VALUE_WEI`, `TICKET_EV_WEI` and `REDEEMER_MAX_FEE_PER_GAS_WEI`; the renderer derives
the win probability as expected value / face value. A winning ticket must be worth more than
redeeming it costs: rendering fails unless the face value covers the fee cap times 250,000 gas.
A measured Arbitrum redemption used 191,870 gas. The defaults pay 0.0004 ETH with a
one-in-20,000 chance, against a 0.00025 ETH worst-case redemption. Payers send whole tickets and
a fixed-price request keeps no change, so the small 2e10 wei expected value limits overpayment
on a $0.001 request to about 5%. The gateway's signer limits each ticket to
1e11 wei of expected value and a fixed price to $0.01 (`config/signer.toml`), refusing
coarser or costlier terms before payment. Review these and upstream node
requirements before paid deployment. The keystore account
needs gas; the gateway payer needs deposit/reserve. Rendering configuration never registers an
orchestrator, funds accounts or moves money. Keep signing and orchestrator identities separate.
The orchestrator container currently runs as root to read operator-owned 0600 secrets; restrict
host access and map ownership if changing its runtime UID.

## Verification and operations

`make check` is offline. `make test-live` exercises both SDKs against a running gateway using
`GATEWAY_TEST_URL`, `GATEWAY_MODEL_NAME` and `GATEWAY_API_KEY`. The accounting black-box suite is:

```sh
make test-accounting-live
```

Supply `GATEWAY_TEST_KEY_A`, `GATEWAY_TEST_KEY_A2` (same user), `GATEWAY_TEST_KEY_B` (another user),
`GATEWAY_TEST_KEY_ADMIN`, and expected `GATEWAY_TEST_INPUT_RATE`, `GATEWAY_TEST_OUTPUT_RATE`,
`GATEWAY_TEST_CACHED_RATE`. Set `GATEWAY_TEST_EXPECT_PAID=1` to require positive observed costs.
The suite makes 14 inference requests; live calls consume funds. It checks response usage,
retail arithmetic, report totals, user isolation and wholesale evidence. It does not prove
final ticket redemption settlement. Offline CI runs the same HTTP suite with a fake backend.

Use `/healthz` for liveness and `/readyz` for backend readiness. Retain and back up complete
volumes with services stopped. The gateway is single-process/single-replica; do not share its
database with another instance. Only the orchestrator deployment requires a GPU. Compose
validation does not establish GPU inference or on-chain readiness; those require operator
hardware/credentials and a separate acceptance run.
