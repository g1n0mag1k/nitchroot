"""
Async SQLAlchemy 2.0 database session factory.

Provides:
  - Async engine configured from settings
  - AsyncSessionLocal factory for use in request handlers
  - get_db() FastAPI dependency that yields a session per request
  - Base declarative model class for all ORM models
"""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import (
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


def _build_engine() -> Any:
    """
    Construct the async SQLAlchemy engine from application settings.

    Returns:
        An AsyncEngine instance configured for the current environment.
    """
    settings = get_settings()
    return create_async_engine(
        str(settings.database_url),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=True,          # Detect stale connections before use
        echo=settings.db_echo,       # Log SQL in development if needed
        future=True,
    )


# Module-level singletons — created once per process
engine = _build_engine()

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # Avoid lazy-load errors after commit
    autoflush=False,
    autocommit=False,
)


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
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("db_session_error", detail="Rolling back transaction")
            raise
        finally:
            await session.close()
