"""Worker process entrypoint (APScheduler probe runner)."""

from __future__ import annotations

import asyncio
import signal

from app.core.logging import configure_logging, get_logger

from workers.scheduler import ProbeScheduler

logger = get_logger(__name__)


async def _main() -> None:
    configure_logging()
    scheduler = ProbeScheduler()
    scheduler.start()
    await scheduler.refresh_merchants()
    logger.info("worker_started")

    stop_event = asyncio.Event()

    def _request_shutdown() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_shutdown)

    await stop_event.wait()
    scheduler.shutdown()
    logger.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(_main())
