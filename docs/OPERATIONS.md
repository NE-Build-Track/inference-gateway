# Run and diagnose

Run `uv sync --locked`, then `uv run python -m endpoint_gateway`. The process binds to
loopback by default. Set `--host` and `--port` explicitly when another listener is needed.
Use a different port per worktree. Stop with Ctrl-C; requests and generator resources
are closed by the server's shutdown and cancellation paths.

| Setting | Default | Behavior |
| --- | --- | --- |
| `GATEWAY_API_KEY` | Unset | Optional shared key, checked on both generation routes |
| `GATEWAY_TIMEOUT_SECONDS` | `30` | Positive finite generation deadline |
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
architecture rules. `GET /healthz` proves process liveness only; it does not probe a future
upstream backend. There is no database to migrate or reset.
