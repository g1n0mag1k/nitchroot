"""
Shared pytest fixtures for the SnitchRoot test suite.

Provides:
  - settings_override: forces test environment settings
  - mock_redis: async Redis mock (no live connections)
  - test_app: FastAPI test application instance
  - client: async HTTPX test client
"""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from app.core.config import get_settings
from app.main import create_app
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def reset_rate_limits() -> None:
    """Clear slowapi counters between tests."""
    from app.core.rate_limit import limiter

    if hasattr(limiter, "reset"):
        limiter.reset()
    yield
    if hasattr(limiter, "reset"):
        limiter.reset()


@pytest.fixture(autouse=True)
def settings_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Force test-safe settings for every test.

    Clears the lru_cache so each test session gets a fresh Settings
    instance loaded from the test environment variables.
    """
    get_settings.cache_clear()
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
    yield
    get_settings.cache_clear()
    # Clear Redis pool cache so later tests never reuse a real/stale pool.
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
