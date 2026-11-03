# Deployment

For ReportingServer with the existing Railway engine, use the
[remote media gateway profile](remote/README.md). It needs caller credentials rather
than signer or Batteries configuration on this host.

The standalone Compose deployments and operator instructions are in
[deploy/livepeer](livepeer/README.md). Use the gateway stack for API serving and payment
accounting, and the orchestrator stack for local vLLM inference.
