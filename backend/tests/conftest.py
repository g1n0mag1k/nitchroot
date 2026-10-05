"""
Shared pytest fixtures for the SnitchRoot test suite.

Provides:
  - settings_override: forces test environment settings
  - mock_redis: async Redis mock (no live connections)
  - test_app: FastAPI test application instance
  - client: async HTTPX test client
  - db_session: async SQLAlchemy session with schema created per test
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest
from app.core.config import get_settings
from app.core.database import Base, get_db
from app.main import create_app
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
    try:
        from app.core.database import get_engine, get_session_factory

        get_engine.cache_clear()
        get_session_factory.cache_clear()
    except ImportError:
        pass
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
async def db_engine():
    """Create an async engine and schema for one test."""
    import app.models  # noqa: F401 — register ORM metadata

    settings = get_settings()
    engine = create_async_engine(str(settings.database_url), pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session bound to the per-test engine."""
    session_factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
async def test_app(
    mock_redis: AsyncMock,
    db_engine,
) -> AsyncGenerator[FastAPI, None]:
    """Yield a FastAPI app with Redis and DB dependencies overridden."""
    from app.core.redis import get_redis

    session_factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_redis] = lambda: mock_redis
    app.dependency_overrides[get_db] = _override_get_db
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


@pytest.fixture
async def registered_merchant(client: AsyncClient) -> dict[str, str]:
    """Register a merchant and return merchant_id and api_key."""
    response = await client.post(
        "/api/v1/merchants",
        json={
            "name": "Fixture Merchant",
            "stripe_restricted_key": "rk_test_fixture_key",
        },
    )
    assert response.status_code == 201
    body = response.json()
    return {"merchant_id": body["merchant_id"], "api_key": body["api_key"]}


@pytest.fixture
def registered_merchant_id(registered_merchant: dict[str, str]) -> uuid.UUID:
    return uuid.UUID(registered_merchant["merchant_id"])


@pytest.fixture
def auth_headers(registered_merchant: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {registered_merchant['api_key']}"}
