# SnitchRoot backend image — development (volume-mounted code) and production targets.
# Build from repository root: docker build -f backend/Dockerfile .

FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./

# --------------------------------------------------------------------------- #
# Dependency layer (runtime only — no dev extras)
# --------------------------------------------------------------------------- #
FROM base AS deps

RUN pip install --no-cache-dir --prefix=/install .

# --------------------------------------------------------------------------- #
# Local development — install dev deps; app code mounted at /app
# --------------------------------------------------------------------------- #
FROM base AS development

RUN pip install --no-cache-dir -e ".[dev]"

WORKDIR /app
ENV PYTHONPATH=/app

# --------------------------------------------------------------------------- #
# Production — non-root, baked application code, no bind mounts
# --------------------------------------------------------------------------- #
FROM python:3.12-slim-bookworm AS production

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN groupadd --gid 1001 snitchroot \
    && useradd --uid 1001 --gid 1001 --create-home --shell /usr/sbin/nologin snitchroot

WORKDIR /app

COPY --from=deps /install /usr/local
COPY backend/app ./app
COPY backend/workers ./workers

USER 1001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
