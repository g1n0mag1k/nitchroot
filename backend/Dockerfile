# SnitchRoot API and worker image (shared base).
# Build from repository root: docker compose build

FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /build/
COPY backend/app /build/backend/app
COPY backend/workers /build/backend/workers

RUN pip install /build/

FROM base AS development

COPY backend/app ./app
COPY backend/workers ./workers

FROM development AS production
