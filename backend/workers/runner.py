"""Background worker entrypoint for scheduled probes and reconciliation."""

from __future__ import annotations

import asyncio
import signal

from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


async def _run() -> None:
    """Worker main loop placeholder until APScheduler wiring lands."""
    configure_logging()
    logger.info("worker_started")
    stop = asyncio.Event()

    def _request_shutdown() -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _request_shutdown)

    await stop.wait()
    logger.info("worker_shutdown")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
