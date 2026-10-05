"""Shared APScheduler instance for worker jobs."""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler

_scheduler: AsyncIOScheduler | None = None


def set_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Register the process-wide AsyncIOScheduler."""
    global _scheduler
    _scheduler = scheduler


def get_scheduler() -> AsyncIOScheduler | None:
    """Return the registered scheduler, if any."""
    return _scheduler
