"""APScheduler jobs for reconciliation queue maintenance."""

from __future__ import annotations

import redis.asyncio as aioredis
import structlog
from app.core.config import Settings, get_settings
from app.reconciliation.job_service import list_pending_jobs, recover_stale_processing_jobs
from app.reconciliation.queue import enqueue_reconciliation_job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger(__name__)


class ReconciliationScheduler:
    """Periodically recovers stale jobs and enqueues pending durable work."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        redis_client: aioredis.Redis,  # type: ignore[type-arg]
        settings: Settings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._redis = redis_client
        self._settings = settings or get_settings()
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    def start(self) -> None:
        """Register interval jobs and start the scheduler."""
        self._scheduler.add_job(
            self.scan_pending_jobs,
            "interval",
            seconds=self._settings.scheduler_pending_scan_interval_seconds,
            id="recon_pending_scan",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self.recover_stale_jobs,
            "interval",
            seconds=self._settings.scheduler_stale_recovery_interval_seconds,
            id="recon_stale_recovery",
            replace_existing=True,
        )
        self._scheduler.start()
        log.info("reconciliation_scheduler_started")

    def shutdown(self, *, wait: bool = True) -> None:
        """Stop scheduling new maintenance work."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)
        log.info("reconciliation_scheduler_stopped")

    async def scan_pending_jobs(self) -> None:
        """Enqueue durable PENDING jobs that are not already marked queued."""
        async with self._session_factory() as session:
            async with session.begin():
                pending = await list_pending_jobs(session)
        enqueued = 0
        for job in pending:
            if await enqueue_reconciliation_job(self._redis, job.id, set_marker=True):
                enqueued += 1
        if enqueued:
            log.info("reconciliation_scheduler_enqueued", count=enqueued)

    async def recover_stale_jobs(self) -> None:
        """Reset stale PROCESSING jobs to PENDING and enqueue them."""
        recovered = 0
        async with self._session_factory() as session:
            async with session.begin():
                recovered = await recover_stale_processing_jobs(session, settings=self._settings)
        if recovered:
            async with self._session_factory() as session:
                async with session.begin():
                    pending = await list_pending_jobs(session)
                for job in pending:
                    await enqueue_reconciliation_job(self._redis, job.id, set_marker=True)
