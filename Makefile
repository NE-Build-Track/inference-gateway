.PHONY: setup run check format
setup:
	uv sync --locked
run:
	uv run python -m inference_gateway
check:
	uv run ruff check src tests scripts live-runner/tooling live-runner/tests
	uv run ruff format --check src tests scripts live-runner/tooling live-runner/tests
	uv run mypy
	uv run pytest -q
	uv run python scripts/check_docs.py
format:
	uv run ruff check --fix src tests scripts live-runner/tooling live-runner/tests
	uv run ruff format src tests scripts live-runner/tooling live-runner/tests

.PHONY: test-live
test-live:
	uv run python scripts/test-live-provider.py

.PHONY: test-accounting-live
test-accounting-live:
	uv run python scripts/test-accounting-http.py
