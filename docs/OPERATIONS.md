# Run and diagnose

Run `uv sync --locked`, then `uv run python -m endpoint_gateway`. The process binds to
loopback by default. Set `--host` and `--port` explicitly when another listener is needed.
Use a different port per worktree. Stop with Ctrl-C; requests and generator resources
are closed by the server's shutdown and cancellation paths.

| Setting | Default | Behavior |
| --- | --- | --- |
| `GATEWAY_API_KEY` | Unset | Optional shared key, checked on both generation routes |
| `GATEWAY_TIMEOUT_SECONDS` | `300` | Positive finite generation deadline |
| CLI `--host` | `127.0.0.1` | Listen address |
| CLI `--port` | `8000` | Listen port |

Authentication is disabled when the key is unset. This initial local scaffold has no
TLS termination, tenant isolation, rate limiting, or request-size cap. Add those controls
before making it a shared service. Do not put real secrets in committed files.

The process entry point enables application logs with JSON payloads. Records contain
a request ID, outcome, protocol where available, and elapsed time for completed work.
They exclude prompt bodies, tool arguments/results, keys, and raw upstream errors.
HTTP headers return the ID for correlation. Logs are ephemeral unless the operator
collects them. Metrics, traces, and validation-error counters are not yet implemented.

For HTTP 400, compare the request with the [compatibility contract](product-specs/compatibility.md).
Unknown fields fail deliberately. For Anthropic, include its version header. For 401,
check the configured shared key and the protocol's authentication header. For 502,
check the backend implementation and its event contract. For 504, inspect backend latency
before changing the deadline. A stream error after HTTP 200 means the generation failed;
clients must not treat partial content as a completed response.

Run `make check` to exercise SDK contracts, network streaming, fault handling, and
architecture rules. `GET /healthz` proves process liveness only; it does not probe upstream availability. Livepeer mode has an engine-owned database that migrates on startup and must be retained.
`GET /readyz` checks engine discovery freshness and candidates for every configured route;
it does not check payment balance. Run one engine process per database.

Use the [Livepeer guide](design-docs/livepeer.md) for backend selection and configuration,
and the [deployment guide](../deploy/README.md) for Compose, certificate trust, and reusing
ChatLPT's existing payment stack. The CLI does not implicitly load `deploy/.env`; Compose
loads it. For host execution, supply environment variables with signer/Batteries URLs that
are reachable from the host.

Gateway logs connect `request_id` to `job_id` through `livepeer_job_started` and
`livepeer_job_closed`. `livepeer_usage_report_failed` retains counts for reconciliation;
network cost stays in the engine and may arrive later. A 503 indicates unavailable capacity
or payment infrastructure; a 409 means the operation was already dispatched. The gateway
adds no retries. See the Livepeer guide for cancellation during stream opening.
