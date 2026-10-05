"""Worker process entrypoint (`python -m workers.runner`)."""

from __future__ import annotations

import asyncio
import signal

import redis.asyncio as aioredis
import structlog
from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.logging import configure_logging, get_logger
from app.core.redis import _get_redis_pool

from workers.reconciliation_worker import ReconciliationWorker
from workers.scheduler import ReconciliationScheduler

logger = get_logger(__name__)


async def _run() -> None:
    configure_logging()
    settings = get_settings()
    structlog.contextvars.bind_contextvars(environment=settings.environment.value)

    pool = _get_redis_pool()
    redis_client = aioredis.Redis(connection_pool=pool, decode_responses=True)

    worker = ReconciliationWorker(AsyncSessionLocal, redis_client, settings=settings)
    scheduler = ReconciliationScheduler(AsyncSessionLocal, redis_client, settings=settings)
    scheduler.start()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, worker.request_shutdown)

    try:
        await worker.run_until_shutdown()
    finally:
        scheduler.shutdown(wait=True)
        await redis_client.aclose()
        await pool.disconnect()
        logger.info("worker_runner_shutdown_complete")


def main() -> None:
    """CLI entrypoint."""
    asyncio.run(_run())


if __name__ == "__main__":
    main()
