# syntax=docker/dockerfile:1
# Python-only gateway image; preserves ChatLPT's Python and uv build pins.
FROM python:3.12-slim-bookworm AS python
COPY --from=ghcr.io/astral-sh/uv:0.12.12 /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm AS app
RUN groupadd --gid 10001 gateway && useradd --uid 10001 --gid gateway --no-create-home gateway \
    && mkdir -p /data && chown 10001:10001 /data
WORKDIR /app
COPY --from=python /app/.venv /app/.venv
COPY --from=python /app/src /app/src
ENV PATH=/app/.venv/bin:$PATH \
    LPB_DATABASE_URL=sqlite+aiosqlite:////data/livepeer-builder.db
VOLUME ["/data"]
USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM
CMD ["python", "-m", "endpoint_gateway", "--host", "0.0.0.0", "--port", "8000"]
