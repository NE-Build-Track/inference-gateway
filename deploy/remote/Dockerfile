# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.12 /uv /bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock .python-version README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --extra batch --extra remote

FROM python:3.12-slim-bookworm
RUN groupadd --gid 10001 gateway && useradd --uid 10001 --gid gateway --no-create-home gateway \
    && mkdir -p /data && chown 10001:10001 /data
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src
ENV PATH=/app/.venv/bin:$PATH
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "inference_gateway.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--root-path", "/inference", "--no-access-log"]
