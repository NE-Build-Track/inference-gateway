# Run and diagnose

Run `uv sync --locked`, then `uv run python -m inference_gateway`. The process binds to
loopback by default. Set `--host` and `--port` explicitly when another listener is needed.
Use a different port per worktree. Stop with Ctrl-C; requests and generator resources
are closed by the server's shutdown and cancellation paths.

| Setting | Default | Behavior |
| --- | --- | --- |
| `GATEWAY_API_KEY` | Unset | Legacy shared key; incompatible with registry mode |
| `GATEWAY_ACCOUNTING_DB` | Unset locally; `/data/gateway.db` in Compose | Registry authentication and durable accounting |
| `GATEWAY_TIMEOUT_SECONDS` | `300` | Positive finite generation deadline |
| CLI `--host` | `127.0.0.1` | Listen address |
| CLI `--port` | `8000` | Listen port |

Authentication is disabled only when both identity settings are unset. Registry mode
enforces user/key isolation. TLS termination, rate limiting and request-size caps remain
deployment concerns. Do not put real secrets in committed files.

The process entry point enables application logs with JSON payloads. Records contain
a request ID, outcome, protocol where available, and elapsed time for completed work.
They exclude prompt bodies, tool arguments/results, keys, and raw upstream errors.
HTTP headers return the ID for correlation. Logs are ephemeral unless the operator
collects them. Metrics, traces, and validation-error counters are not yet implemented.

For HTTP 400, compare the request with the [compatibility contract](product-specs/compatibility.md).
Unknown fields fail deliberately. For Anthropic, include its version header. For 401,
check the key, its expiry/revocation, user status and the protocol's authentication header.
A 403 means the key lacks the required scope. For 502,
check the backend implementation and its event contract. For 504, inspect backend latency
before changing the deadline. A stream error after HTTP 200 means the generation failed;
clients must not treat partial content as a completed response.

Run `make check` to exercise SDK contracts, network streaming, fault handling, and
architecture rules. `GET /healthz` proves process liveness only; it does not probe upstream availability. Livepeer mode has an engine-owned database that migrates on startup and must be retained.
`GET /readyz` checks engine discovery freshness and candidates for every configured route;
it does not check payment balance. Run one engine process per database.

Use the [Livepeer guide](design-docs/livepeer.md) for backend selection and configuration,
and the [deployment guide](../deploy/README.md) for Compose, certificate trust, and reusing
the standalone payment stack. The CLI does not implicitly load `deploy/livepeer/gateway/.env`; Compose
loads it. For host execution, supply environment variables with signer/Batteries URLs that
are reachable from the host.

Gateway logs connect `request_id` to `job_id` through `livepeer_job_started` and
`livepeer_job_closed`. `livepeer_usage_report_failed` retains counts for reconciliation;
network cost stays in the engine and may arrive later. A 503 indicates unavailable capacity
or payment infrastructure; a 409 means the operation was already dispatched. The gateway
does not retry inference. Registry mode retries accounting work separately; inspect
`reconcile_failures` in admin request reports and use `gateway accounting retry REQUEST_ID`
after repairing the cause. See the Livepeer guide for cancellation during stream opening.

See [accounting](design-docs/accounting.md) for exact monetary semantics and the CLI runbook.


## Capability probes

`scripts/probe-livepeer-capabilities.py` bypasses the gateway's extended-feature guard and sends
bounded runner requests through the public Builder Core API. Use it in an environment with the
same `LPB_*` payment/discovery settings and `GATEWAY_MODEL_ROUTES` as the intended deployment.
It creates a separate temporary engine database and engine ID; it never opens the production
engine database or changes the running gateway. Requests are paid, sequential and use one attempt.

```sh
uv run python scripts/probe-livepeer-capabilities.py --run --output capability-probes.json \
  | tee capability-probes.jsonl
```

Use `--model NAME` to select a configured route or repeat `--feature NAME` to limit cases. A
baseline runs first; failure leaves remaining features unverified without dispatch. To reuse a
successful same-route baseline less than one hour old, pass `--baseline-report PATH`. This starts
new feature operations, never retries the failed operation. Keep the output report and isolated
engine database for payment/job evidence. Temporary evidence in a container may disappear at restart;
copy it to durable operator storage if needed.

Reports contain operation/job IDs and structural signals, never raw prompts, tool arguments,
responses, credentials or exceptions. `observed` means the probe saw the requested wire shape,
not full feature certification. Accepted sampling, effort/budget controls and audio input remain
`inconclusive` without semantic evidence. The image-input probe uses a valid 128px blue PNG and
checks the color answer; the audio-input probe uses one second of valid WAV silence and tests only
acceptance. Reports are saved after every case, so interrupted runs retain completed evidence. `Unsupported Response` means a completed response failed the probe's
output contract; an outage or timeout must not be recorded as an unsupported capability.
The script never enables a feature in the gateway automatically. Record results in the
[gap register](exec-plans/livepeer-capability-gaps.md).


## Extended Livepeer acceptance

Enable the intended route's capabilities as described in the
[contract](product-specs/extended-generation.md#livepeer-route-capabilities), and validate an
isolated gateway before changing shared traffic. `scripts/test-live-extended.py` sends paid
requests through the public gateway and both official SDKs in JSON/SSE modes. It disables SDK
retries and writes sanitized results after every case; use a durable host output path.

```sh
uv run python scripts/test-live-extended.py --run --url http://127.0.0.1:8001 \
  --key-file /path/to/private-test-key --output live-extended-acceptance.json
```

The key file must contain only the secret. Alternatively use `GATEWAY_API_KEY` in the test
process. `--feature`, `--protocol`, and `--mode` limit the cases; `--model` selects a route.
Reports retain request IDs, token counts and structural assertions, never model output or tool
arguments. A 503 or timeout is operational evidence, not proof of an unsupported capability.
Check engine attempt records for payment state before deciding whether another test is appropriate.

If a runner fails TLS verification, keep verification enabled. Restore existing public trust
pins with `scripts/prepare-runner-trust.py` when needed, or select another already trusted,
approved orchestrator with `GATEWAY_APPROVED_ORCHESTRATORS`. Do not replace trust pins solely
because an endpoint presents a different certificate.

## Models and Responses rollout

The new routing contract requires each runner to register JSON metadata containing the exact
served model name, for example `{"model_name":"gemma4:26b"}`. Update runner registration first.
Do not replace missing metadata with the app name. Configure the gateway's approved orchestrators,
existing signer/payment settings and retail prices as before, then configure the model route:

```sh
# Replace your-team/gemma-app with the actual registered application ID.
GATEWAY_MODEL_ROUTES='[{"name":"gemma4:26b","app":"your-team/gemma-app","upstream_api":"responses","capabilities":["parallel_tools","media_input"],"input_media":["image"]}]'
```

Declare only capabilities verified on every matching runner in that model/app route. Use
`upstream_api:"chat_completions"` to expose the stateless Responses subset through a chat runner;
use `responses` when the runner implements the native endpoint. Duplicate names on different
apps add alternative offerings, selected before paid dispatch. Names must match `model_name`;
legacy aliases and different `upstream_model` values are no longer accepted.

Build a candidate without replacing the published v0.0.1 image:

```sh
docker build -f deploy/livepeer/gateway/Dockerfile -t inference-gateway/gateway:models-responses .
```

Use that tag in an isolated deployment with a separate engine ID/database and port while retaining
approved TLS trust. Readiness requires eligible model metadata. A 200 empty `/v1/models` list can
mean no approved available supply or no retail price; 401/403 indicates authentication/scope issues.
The endpoint uses Bearer authentication, including for clients that otherwise use Anthropic Messages.

After configuring an inference key, save it in a private key file and run the opt-in acceptance:

```sh
uv run python scripts/test-live-responses.py --run --url http://127.0.0.1:8001 \
  --model gemma4:26b --key-file /private/gateway-key \
  --output /private/responses-acceptance.json
```

The script checks model visibility and SDK text generation in JSON and SSE modes, preserving only
structural results and token counts. It does not certify tool/image behavior or reconcile the
retail ledger. Also run the client's tool/image workflow and correlate each response request ID
with one engine job and one retail usage charge. Verify timeout/disconnect behavior before rollout.
Do not call raw runner endpoints directly as a substitute for gateway payment/accounting acceptance.
