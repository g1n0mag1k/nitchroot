"""APScheduler AsyncIOScheduler wired into FastAPI lifespan.

Exports:
  - build_scheduler(): creates and returns a configured AsyncIOScheduler
  - lifespan(): FastAPI lifespan context manager that starts/stops the scheduler
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI

log = structlog.get_logger(__name__)

_PROBE_INTERVAL_SECONDS = 300  # 5 minutes
_RECONCILE_INTERVAL_SECONDS = 300  # 5 minutes


def build_scheduler() -> AsyncIOScheduler:
    """Create an AsyncIOScheduler with SnitchRoot jobs pre-registered.

    Jobs are registered here but the scheduler is NOT started.
    Call scheduler.start() explicitly (or use the lifespan context manager).

    Returns:
        Configured AsyncIOScheduler instance.
    """
    scheduler = AsyncIOScheduler()

    # Probe heartbeat job — placeholder; real merchants come from DB in prod
    scheduler.add_job(
        _probe_heartbeat,
        trigger=IntervalTrigger(seconds=_PROBE_INTERVAL_SECONDS),
        id="probe_heartbeat",
        name="Zero-fee Stripe probe heartbeat",
        replace_existing=True,
        misfire_grace_time=60,
        coalesce=True,
    )

    # Reconciliation sweep job
    scheduler.add_job(
        _reconciliation_sweep,
        trigger=IntervalTrigger(seconds=_RECONCILE_INTERVAL_SECONDS),
        id="reconciliation_sweep",
        name="Ledger reconciliation sweep",
        replace_existing=True,
        misfire_grace_time=60,
        coalesce=True,
    )

    log.info("scheduler_built", job_count=len(scheduler.get_jobs()))
    return scheduler


async def _probe_heartbeat() -> None:
    """Scheduled probe heartbeat — logs liveness, real probe logic added in prod."""
    log.info("probe_heartbeat_tick")


async def _reconciliation_sweep() -> None:
    """Scheduled reconciliation sweep — logs liveness, real logic added in prod."""
    log.info("reconciliation_sweep_tick")


@asynccontextmanager
async def scheduler_lifespan(
    app: FastAPI,
) -> AsyncGenerator[None, None]:
    """FastAPI lifespan context manager that owns the scheduler lifecycle.

    Usage in main.py:
        app = FastAPI(lifespan=scheduler_lifespan)

    Yields:
        None — the scheduler runs in the background during app lifetime.
    """
    scheduler = build_scheduler()
    try:
        scheduler.start()
        log.info("scheduler_started")
        yield
    finally:
        scheduler.shutdown(wait=False)
        log.info("scheduler_stopped")
