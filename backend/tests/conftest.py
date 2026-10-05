"""
Shared pytest fixtures for the SnitchRoot test suite.

Provides:
  - settings_override: forces test environment settings
  - mock_redis: async Redis mock (no live connections)
  - test_app: FastAPI test application instance
  - client: async HTTPX test client
"""

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import redis.asyncio as aioredis
from app.core.config import get_settings
from app.core.database import Base
from app.main import create_app
from app.persistence import models as _persistence_models  # noqa: F401
from app.reconciliation.queue import QUEUED_JOB_MARKER_PREFIX, RECONCILIATION_JOBS_QUEUE
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


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


@pytest.fixture(autouse=True)
def mock_webhook_persistence(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Prevent webhook API tests from requiring live PostgreSQL."""
    mock = AsyncMock(return_value=(uuid.uuid4(), uuid.uuid4()))
    monkeypatch.setattr(
        "app.api.webhooks.persist_webhook_and_enqueue_reconciliation",
        mock,
    )
    return mock


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Return an AsyncMock stand-in for redis.asyncio.Redis."""
    return AsyncMock()


@pytest.fixture
async def test_app(mock_redis: AsyncMock) -> AsyncGenerator[FastAPI, None]:
    """Yield a FastAPI app with Redis dependency overridden (no live Redis)."""
    from app.core.database import get_db
    from app.core.redis import get_redis

    async def _override_db() -> AsyncGenerator[AsyncMock, None]:
        session = AsyncMock()
        yield session

    app = create_app()
    app.dependency_overrides[get_redis] = lambda: mock_redis
    app.dependency_overrides[get_db] = _override_db
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


async def _service_reachable(url: str) -> bool:
    if url.startswith("postgresql"):
        try:
            from sqlalchemy import text

            engine = create_async_engine(url, pool_pre_ping=True)
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            await engine.dispose()
            return True
        except OSError:
            return False
        except Exception:
            return False
    if url.startswith("redis"):
        try:
            client = aioredis.from_url(url, decode_responses=True)
            await client.ping()
            await client.aclose()
            return True
        except OSError:
            return False
        except Exception:
            return False
    return False


@pytest.fixture(scope="session")
def postgres_available() -> bool:
    """True when the configured PostgreSQL DSN accepts connections."""
    get_settings.cache_clear()
    settings = get_settings()
    import asyncio

    return asyncio.run(_service_reachable(str(settings.database_url)))


@pytest.fixture(scope="session")
def redis_available() -> bool:
    """True when the configured Redis URL accepts connections."""
    get_settings.cache_clear()
    settings = get_settings()
    import asyncio

    return asyncio.run(_service_reachable(str(settings.redis_url)))


@pytest.fixture
async def integration_engine(postgres_available: bool) -> AsyncGenerator:
    if not postgres_available:
        pytest.skip("PostgreSQL is not available for integration tests")
    settings = get_settings()
    engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def integration_session_factory(integration_engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=integration_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def integration_redis(redis_available: bool) -> AsyncGenerator[aioredis.Redis, None]:
    if not redis_available:
        pytest.skip("Redis is not available for integration tests")
    settings = get_settings()
    client = aioredis.from_url(str(settings.redis_url), decode_responses=True)
    await client.delete(RECONCILIATION_JOBS_QUEUE)
    keys = await client.keys(f"{QUEUED_JOB_MARKER_PREFIX}*")
    if keys:
        await client.delete(*keys)
    yield client
    await client.delete(RECONCILIATION_JOBS_QUEUE)
    keys = await client.keys(f"{QUEUED_JOB_MARKER_PREFIX}*")
    if keys:
        await client.delete(*keys)
    await client.aclose()
