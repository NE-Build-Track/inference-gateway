# Documentation index

Repository maintainers own these documents. Update the affected contract, design,
and tests together when behavior changes. Verification status is in [Quality](QUALITY.md).

- [Architecture](../ARCHITECTURE.md): module boundaries and dependency direction.
- [Compatibility](product-specs/compatibility.md): requests, output, and exclusions.
- [Backend guide](design-docs/backends.md): implement and inject a backend.
- [Decisions](design-docs/decisions.md): assumptions, tradeoffs, and revisit triggers.
- [Operations](OPERATIONS.md): configuration and troubleshooting.
- [Quality](QUALITY.md): current evidence and gaps.
- [Execution plans](exec-plans/index.md): work history and planning procedure.
- [Technical debt](exec-plans/tech-debt-tracker.md): remaining work.
- [References](references/index.md): checked upstream contracts and requested design sources.

- [Deployment scaffold](../deploy/README.md): standalone Compose stacks and operator prerequisites.
- [Livepeer backend](design-docs/livepeer.md): model routing, engine ownership, and paid stream semantics.

- [Users and accounting](design-docs/accounting.md): keys, retail/wholesale and reporting.

- [Extended generation contracts](product-specs/extended-generation.md): reasoning, sampling,
  structured output, citations, hosted search, interleaving and media.
- [Livepeer capability gaps](exec-plans/livepeer-capability-gaps.md): probe evidence and adapter work.

- [Models and Responses](product-specs/models-and-responses.md): metadata routing and stateless API contract.

- [Native inference direction](design-docs/native-inference-routing.md): vLLM, Ollama and SGLang; runner metadata, lean forwarding and accounting boundaries.

- [Live-runner bundle](../live-runner/README.md): candidate contract, pinned runtimes and readiness.
- [Native transport boundary](design-docs/native-transport-integration.md): implemented components and the NE dependency review chain.

- [Native inference contract](product-specs/native-inference.md): explicit route mode, admission, forwarding and accounting.
