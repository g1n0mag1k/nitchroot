"""
Background worker entrypoint.

Verifies PostgreSQL and Redis connectivity, then blocks until shutdown.
Scheduled probe and reconciliation jobs attach here in later phases.
"""

from __future__ import annotations

import asyncio
import signal

from app.core.config import get_settings
from app.core.database import engine
from app.core.logging import configure_logging, get_logger
from app.core.redis import get_redis
from sqlalchemy import text

_ready = False


def is_ready() -> bool:
    """Return True after dependency checks have succeeded."""
    return _ready


async def _verify_dependencies() -> None:
    redis = await get_redis()
    await redis.ping()
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _run() -> None:
    global _ready
    configure_logging()
    settings = get_settings()
    logger = get_logger(__name__)
    logger.info(
        "worker_starting",
        environment=settings.environment.value,
    )
    await _verify_dependencies()
    _ready = True
    logger.info("worker_ready")

    stop = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("worker_shutdown_signal")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    await stop.wait()
    logger.info("worker_stopped")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
