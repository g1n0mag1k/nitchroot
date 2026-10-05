"""Redis async client factory and FastAPI dependency."""

from __future__ import annotations

from functools import lru_cache

import redis.asyncio as aioredis

from app.core.config import get_settings


@lru_cache(maxsize=1)
def _get_redis_pool() -> aioredis.ConnectionPool:  # type: ignore[type-arg]
    """Return a process-wide Redis connection pool (cached).

    Returns:
        A redis.asyncio ConnectionPool configured from settings.
    """
    settings = get_settings()
    return aioredis.ConnectionPool.from_url(
        str(settings.redis_url),
        decode_responses=True,
        max_connections=20,
    )


async def get_redis() -> aioredis.Redis:  # type: ignore[type-arg]
    """FastAPI dependency: yields a Redis client from the shared pool.

    Returns:
        An async Redis client bound to the shared connection pool.
    """
    pool = _get_redis_pool()
    # decode_responses=True selects the Redis[str] overload for type checkers;
    # omitted from runtime-only kwargs already set on the shared pool.
    return aioredis.Redis(connection_pool=pool, decode_responses=True)
