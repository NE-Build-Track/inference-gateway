.PHONY: setup run check format
setup:
	uv sync --locked
run:
	uv run python -m endpoint_gateway
check:
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts
	uv run mypy
	uv run pytest -q
	uv run python scripts/check_docs.py
format:
	uv run ruff check --fix src tests scripts
	uv run ruff format src tests scripts
