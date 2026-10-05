"""
Async SQLAlchemy 2.0 database session factory.

Provides:
  - Async engine configured from settings
  - AsyncSessionLocal factory for use in request handlers
  - get_db() FastAPI dependency that yields a session per request
  - Base declarative model class for all ORM models
"""

from collections.abc import AsyncGenerator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    """
    Shared declarative base for all SnitchRoot ORM models.

    All models must inherit from this class so Alembic can discover
    them during autogenerate.
    """

    # Subclasses define __tablename__ and columns.
    pass


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Return the process-wide async SQLAlchemy engine (lazy singleton)."""
    settings = get_settings()
    return create_async_engine(
        str(settings.database_url),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=True,
        echo=settings.db_echo,
        future=True,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the async session factory bound to :func:`get_engine`."""
    return async_sessionmaker(
        bind=get_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


def __getattr__(name: str) -> AsyncEngine | async_sessionmaker[AsyncSession]:
    if name == "engine":
        return get_engine()
    if name == "AsyncSessionLocal":
        return get_session_factory()
    raise AttributeError(name)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides a database session per request.

    Automatically commits on success and rolls back on any exception.
    Ensures the session is closed regardless of outcome.

    Yields:
        An AsyncSession bound to the current request context.

    Example:
        @router.get("/example")
        async def example(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(SomeModel))
            ...
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("db_session_error", detail="Rolling back transaction")
            raise
        finally:
            await session.close()
