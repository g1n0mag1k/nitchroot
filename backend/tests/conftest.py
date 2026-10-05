"""
Shared pytest fixtures for the SnitchRoot test suite.

Provides:
  - settings_override: forces test environment settings
  - mock_redis: async Redis mock (no live connections)
  - test_app: FastAPI test application instance
  - client: async HTTPX test client
"""

import os
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def pytest_configure() -> None:
    """Set test env vars before application modules (rate limiter) are imported."""
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+asyncpg://snitchroot:snitchroot_test@localhost:5432/snitchroot_test",
    )
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("STRIPE_RESTRICTED_KEY", "rk_test_placeholder_do_not_use")
    os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_placeholder_do_not_use")
    os.environ.setdefault("SECRET_KEY", "a" * 64)
    os.environ.setdefault("RATELIMIT_ENABLED", "false")


@pytest.fixture(autouse=True)
def reset_rate_limits() -> None:
    """Clear cached limiter instances between tests (no Redis flush)."""
    from app.core.rate_limit import get_limiter

    get_limiter.cache_clear()
    yield
    get_limiter.cache_clear()


@pytest.fixture(autouse=True)
def settings_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Force test-safe settings for every test.

    Clears the lru_cache so each test session gets a fresh Settings
    instance loaded from the test environment variables.
    """
    from app.core.config import get_settings
    from app.core.rate_limit import get_limiter

    get_settings.cache_clear()
    get_limiter.cache_clear()
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://snitchroot:snitchroot_test@localhost:5432/snitchroot_test",
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("STRIPE_RESTRICTED_KEY", "rk_test_placeholder_do_not_use")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_placeholder_do_not_use")
    monkeypatch.setenv("SECRET_KEY", "a" * 64)
    monkeypatch.setenv("RATELIMIT_ENABLED", "false")
    yield
    get_settings.cache_clear()
    try:
        from app.core.redis import _get_redis_pool

        _get_redis_pool.cache_clear()
    except ImportError:
        pass


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Return an AsyncMock stand-in for redis.asyncio.Redis."""
    return AsyncMock()


@pytest.fixture
async def test_app(mock_redis: AsyncMock) -> AsyncGenerator[FastAPI, None]:
    """Yield a FastAPI app with Redis dependency overridden (no live Redis)."""
    from app.core.redis import get_redis
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[get_redis] = lambda: mock_redis
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
async def client(test_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """
    Yield an async HTTPX client wired to the test FastAPI app.

    Uses ASGITransport so no real network calls are made.
    """
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://testserver",
    ) as ac:
        yield ac
