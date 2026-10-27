# Verification

[Offline tests](../../tests/test_runner_bundle.py) run in `make check` and need no Docker,
GPU, model download or live network. They exercise all runtime renderers, exact model
identity and revision propagation, generated TOML, metadata bounds, candidate admission,
configuration fingerprint changes, schema/profile synchronization and readiness failures.

`make check` also lints/formats `live-runner/tooling`. The shared contract is included
in strict source typing. Compose configuration can be checked without starting services.

Still required before admission: real pinned-runtime startup; loaded-model identity;
JSON/SSE per endpoint; tool/schema and parser behavior where claimed; usage and cached
usage; native payload/header/status preservation through gateway and node; cancellation,
timeouts, malformed output, paid failures and accounting; verified stateless retention.
Record sanitized results by deployment revision. No evidence file should claim these
checks passed merely because the renderer or readiness observer succeeded.
