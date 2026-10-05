# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /deps

COPY pyproject.toml README.md ./

RUN uv venv /app/.venv \
    && UV_PROJECT_ENVIRONMENT=/app/.venv uv sync --no-dev --no-install-project

ENV PATH="/app/.venv/bin:$PATH" \
    UV_PROJECT_ENVIRONMENT=/app/.venv

FROM base AS development

RUN uv sync --no-install-project

COPY backend/ /app/

WORKDIR /app

RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid 1001 --create-home appuser \
    && chown -R appuser:appgroup /app /app/.venv

USER 1001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health')" \
    || exit 1
