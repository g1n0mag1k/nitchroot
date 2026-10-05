"""
Shared pytest fixtures for the SnitchRoot test suite.

Provides:
  - settings_override: forces test environment settings
  - mock_redis: async Redis mock (no live connections)
  - test_app: FastAPI test application instance
  - client: async HTTPX test client
"""

import os

# Required before app.core.database is imported (engine is created at import time).
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

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from app.core.config import get_settings
from app.core.database import Base
from app.main import create_app
from app.persistence import models as _persistence_models  # noqa: F401
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


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


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Async engine bound to the pytest session event loop."""
    settings = get_settings()
    eng = create_async_engine(
        str(settings.database_url),
        pool_pre_ping=True,
        future=True,
    )
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def db_schema(test_engine: AsyncEngine) -> AsyncGenerator[None, None]:
    """Create persistence tables once per test session."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(loop_scope="session")
async def db_session(
    test_engine: AsyncEngine,
    db_schema: None,
) -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session that rolls back after each test."""
    session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    async with session_factory() as session:
        yield session
        await session.rollback()


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
