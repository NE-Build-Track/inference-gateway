# Repository map

Read [README.md](README.md) for setup and [ARCHITECTURE.md](ARCHITECTURE.md) for boundaries.
The repository is the source of truth; record durable decisions here.

## Find the right context

- [Documentation index](docs/index.md): navigation and ownership.
- [Compatibility contract](docs/product-specs/compatibility.md): supported behavior and limits.
- [Backend guide](docs/design-docs/backends.md): ports, event order, resource ownership.
- [Design decisions](docs/design-docs/decisions.md): rationale and revisit triggers.
- [Quality status](docs/QUALITY.md): verified properties and gaps.
- [Operations](docs/OPERATIONS.md): run, diagnose, and configure.
- [Execution plans](docs/exec-plans/index.md): active work and completed decisions.
- [Technical debt](docs/exec-plans/tech-debt-tracker.md): known limitations.
- [References](docs/references/index.md): API sources and design guidance.

## Working rules

1. Define acceptance behavior before changing a protocol or backend contract.
2. Keep `core` independent of frameworks, protocol DTOs, configuration, and I/O clients.
3. Translate provider formats in `adapters`; select implementations only in `app.py`.
4. Pass collaborators explicitly. Do not add service locators or import-time clients.
5. Preserve cancellation, deadlines, and async-generator cleanup on every backend path.
6. Reject unsupported inputs explicitly. Expand the compatibility document with each feature.
7. Test meaningful behavior with fakes, SDK contracts, and failure cases.
8. Run `make check` before handoff; build the package when packaging changes.
9. Write documentation for the developer's task using the Sense of Style guidance.
10. Capture complex work in `docs/exec-plans/active`; move it to `completed` when verified.
11. Put unresolved limitations in the debt tracker; do not describe planned features as shipped.
12. Do not log prompts, tool arguments/results, secrets, or raw backend exceptions.
13. Never add co-author attribution or trailers to commit messages or PR descriptions.

## Commands

- `uv sync --locked`: install the reproducible environment.
- `make run`: start on loopback port 8000.
- `uv run python -m endpoint_gateway --port 8001`: isolate a worktree's server.
- `make check`: run the same checks as CI.
- `make format`: apply lint fixes and formatting.
- `uv build`: build wheel and source distribution.
