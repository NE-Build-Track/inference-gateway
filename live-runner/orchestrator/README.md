# Livepeer node integration

The renderer produces `orchestrator.toml` and `runners.toml` for the digest-pinned node
in the image lock. Node source provenance is recorded there. This is the previously
published node candidate, not an assertion that it matches a newer local node checkout.

The configuration uses static registration, one single-shot runner, private runtime
and readiness URLs, explicit destination grants, and offchain operation. The default
registration status is `idle`, so a successful health check alone cannot admit it.
No GPU deployment or end-to-end node acceptance was performed during scaffold creation.

The node polls each registration's readiness service, the standard's `runner-contract ready`. It
answers ready once the self-tests of the registration's profiles pass against the runtime, and
follows the runtime's own health check (`/health`, or `/` for Ollama) after that.

Production/payment configuration is intentionally not generated here. Paid admission
must supply node identity, TLS/public ingress, signer/allocation configuration and
payment/cost acceptance separately. Do not treat the offchain node as a production
payment test. Node data is persisted at its image's application data directory.

Keep the node a generic proxy: do not add model schemas or protocol translation to it.
The renderer uses its opaque Metadata field and generic health/capacity mechanisms.
