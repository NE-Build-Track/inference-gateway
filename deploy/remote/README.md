# Remote Railway media gateway

This profile runs the gateway on ReportingServer while the existing Railway Core
owns authentication, funded allocations, network dispatch, payments and accounting.
It does not start an engine, signer or Batteries instance. Requests carry the caller's
Railway Builder bearer credential; no upstream service key is configured on this host.
The current remote adapter serves admitted synchronous media methods only. Chat,
Responses, Messages and asynchronous video are not advertised by this profile.

Build with `docker build -f deploy/remote/Dockerfile -t gateway:reviewed .`.
Copy this profile to a release directory and configure `GATEWAY_IMAGE` with the
resulting immutable image ID in its `.env` file. Create `data` owned by UID 10001.
Populate `catalog` with `capabilities.openapi.json` and `native-approvals.json`
from the admission compiler. The gateway requires both files and matches each
request against authenticated Core discovery, exact deployment identity, input
schema and unexpired admission before dispatch. Renew admission deliberately before
expiry and recreate the container; expiry removes model availability.

Run `docker compose up -d`. Add an Apache `/inference/` reverse proxy to
`http://127.0.0.1:18000/` before the console's `/` route, with a 660-second timeout.
The image configures ASGI root path `/inference`. Its OpenAI SDK base URL is
`https://app.eliteencoder.net/inference/v1`; its OpenAPI document is `/inference/openapi.json`.
Preserve existing console and issuer routes. Check Apache configuration before reload.

There is one upstream submission attempt. A lost response is never resubmitted;
receipts preserve an unknown/failed outcome. Core's job ID is recorded when headers
arrive. Closing the upstream response invokes Core's HTTP disconnect/settlement path.
Completed idempotent responses replay without generating again. Receipts and artifact
ownership are credential-scoped: rotating the bearer credential does not grant access
to artifacts created under the previous credential. Retention defaults to 24 hours.
No retail ledger or invented token usage is added: wholesale costs stay in Core.

Input bodies are limited to Core's 8 MiB decoded request limit; output is limited to
128 MiB. Unsupported parameters fail schema validation. Multipart transcription is
forwarded unchanged. Public media responses retain the runner/provider body shape.
Do not enable automatic proxy retries or log authorization headers and request bodies.

Rollback: restore the previous image/catalog release and recreate only this Compose
service. Keep `data` mounted; incomplete receipts recover as failed and are not rerun.
Deployment verification uses health, schema, model discovery and authorization checks.
Paid requests and cost reconciliation are a separate acceptance step.
