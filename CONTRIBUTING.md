# Contributing to Inference Gateway

We welcome bug fixes, documentation, tests and new capabilities that respect the project's
API contracts and architecture. Discuss large features or changes to billing semantics in
an issue before implementing them. Small, focused pull requests are easier to review.

## Development setup

Fork and clone the repository. Install Python 3.12+ and uv, then run:

```sh
uv sync --locked
make check
uv run python -m inference_gateway --port 8001
```

Offline development uses the fake backend and needs no credentials or GPU. Never run paid
acceptance against another person's allocation. Do not commit `.env` files, issued keys,
wallets, certificate pins or databases. Use the committed environment examples as templates.

## Design and code conventions

Read [AGENTS.md](AGENTS.md) for the repository map and [ARCHITECTURE.md](ARCHITECTURE.md)
for dependency boundaries. Business rules belong in the core; HTTP/provider translations
belong in adapters. Constructors receive collaborators explicitly. Frameworks and backend
SDKs must not enter the core. Keep changes within the supported Python version range.

Preserve streaming, cancellation and resource cleanup. Missing accounting evidence is not
zero cost. Retail and wholesale remain separate; changing prices must not rewrite history.
Do not retry inference to repair accounting. Avoid logging prompts, tool data or secrets.

## Tests and documentation

Describe the expected behavior, add meaningful regression/contract tests, and run:

```sh
make format
make check
uv build
```

For deployment changes, validate the affected Compose files and document any GPU or paid
checks you could not run. Black-box acceptance tests must use public HTTP and CLI interfaces;
they must not read implementation databases to make their assertions pass. Keep paid tests
opt-in and ordinary CI offline.

Update the compatibility/configuration documentation when behavior changes. For substantial
work, maintain an execution plan in `docs/exec-plans/active` and move it to `completed` with
verification evidence. Explain limitations honestly; do not describe planned work as shipped.

## Pull requests

Explain the problem, resulting behavior and validation. Include migration/rollback steps
for durable data or deployment changes. Link the issue when one exists. Use a concise,
descriptive title and keep unrelated refactors separate. CI must pass; maintainers may request
changes for correctness, compatibility, security, readability or maintenance cost.

Use descriptive commit messages. Do not add co-author attribution or commit/PR trailers.
This project does not require a sign-off trailer. Contributed code is submitted under the
repository's MIT license; retain applicable copyright and license notices.
