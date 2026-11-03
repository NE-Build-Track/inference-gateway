# ReportingServer remote Railway backend

Acceptance: deploy the five-model synchronous media catalog without moving Core,
Batteries, signer or their credentials from Railway. Preserve caller authorization,
provider response bodies, raw multipart bytes, bounded I/O, exact schema/deployment
admission, cancellation/disconnect cleanup and durable idempotent receipts. Never
retry an uncertain paid submission. Keep paid tests separate from deployment.

The remote composition uses the existing network-batch router and storage. Each
request authenticates through Core capability discovery and checks the pinned local
catalog against returned profiles plus unexpired operator admission. Only admitted
media routes are installed. SDK requests become one raw-body Core HTTP job using the
same bearer credential. Core remains the authority for funding, permissions and costs.
Credential hashes scope receipts/artifacts; there is no gateway retail charge.

The deployment profile keeps the gateway on loopback behind ReportingServer Apache
under `/inference/`, with separate persistent data and read-only catalog mounts.
Core/console rollout precedes this deployment. Preserve the production console's
OIDC security integration, issuer realm and existing payment stack.

Offline acceptance covers real OpenAI SDK image and multipart transcription calls,
stale/expired admission, unsupported parameters, authorization, credential isolation,
idempotent replay and lost-submission handling. Production verification uses only
health/discovery/auth/schema calls; paid job execution and reconciliation remain open.

Remaining scope: remote conversation protocol translation, stable actor identity
across credential rotation, and paid accounting acceptance. Existing embedded mode
continues serving its prior conversation interfaces.
