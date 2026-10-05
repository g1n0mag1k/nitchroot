"""Reconciliation job worker — execution only, no business rules."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING

import redis.asyncio as aioredis
import structlog
from app.core.config import Settings, get_settings
from app.persistence.enums import JobFailureCategory
from app.reconciliation.exceptions import (
    NonRetryableJobError,
    ReconciliationJobError,
    RetryableJobError,
)
from app.reconciliation.job_service import (
    JobClaimResult,
    claim_reconciliation_job,
    mark_job_completed,
    mark_job_failed,
    recover_stale_processing_jobs,
)
from app.reconciliation.processor import process_reconciliation_job
from app.reconciliation.queue import (
    clear_enqueue_marker,
    dequeue_reconciliation_job,
    enqueue_reconciliation_job,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

if TYPE_CHECKING:
    from asyncio import Task

log = structlog.get_logger(__name__)


class ReconciliationWorker:
    """Consumes Redis job ids and executes process_reconciliation_job()."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        redis_client: aioredis.Redis,  # type: ignore[type-arg]
        settings: Settings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._redis = redis_client
        self._settings = settings or get_settings()
        self._shutdown = asyncio.Event()
        self._in_flight: set[Task[None]] = set()

    def request_shutdown(self) -> None:
        """Signal the worker loop to stop accepting new jobs."""
        self._shutdown.set()

    async def run_until_shutdown(self) -> None:
        """Main consume loop."""
        log.info("reconciliation_worker_started")
        await self._recover_stale_on_startup()
        while not self._shutdown.is_set():
            job_id = await dequeue_reconciliation_job(
                self._redis, timeout_seconds=self._settings.worker_poll_timeout_seconds
            )
            if job_id is None:
                continue
            task = asyncio.create_task(self._execute_job(job_id))
            self._in_flight.add(task)
            task.add_done_callback(self._in_flight.discard)
        await self._drain_in_flight()

    async def _recover_stale_on_startup(self) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                await recover_stale_processing_jobs(session, settings=self._settings)

    async def _drain_in_flight(self) -> None:
        if not self._in_flight:
            return
        log.info("reconciliation_worker_draining", in_flight=len(self._in_flight))
        await asyncio.wait(
            self._in_flight,
            timeout=self._settings.worker_shutdown_grace_seconds,
        )

    async def _execute_job(self, job_id: uuid.UUID) -> None:
        start = time.monotonic()
        bound_log = log.bind(job_id=str(job_id))
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    claim: JobClaimResult = await claim_reconciliation_job(
                        session, job_id, settings=self._settings
                    )
                    if claim.reason == "already_completed":
                        bound_log.info("reconciliation_job_skip_completed")
                        return
                    if claim.reason in {
                        "not_found_or_locked",
                        "already_processing",
                        "failed_non_retryable",
                        "failed_max_attempts",
                    }:
                        bound_log.info(
                            "reconciliation_job_skip",
                            reason=claim.reason,
                        )
                        return
                    if claim.job is None:
                        return

                    merchant_id = str(claim.job.merchant_id)
                    bound_log = bound_log.bind(merchant_id=merchant_id)
                    try:
                        report = await process_reconciliation_job(session, job_id)
                        await mark_job_completed(session, claim.job, report)
                        bound_log.info(
                            "reconciliation_job_completed",
                            status=report.status.value,
                            duration_ms=int((time.monotonic() - start) * 1000),
                        )
                    except NonRetryableJobError as exc:
                        await mark_job_failed(
                            session,
                            claim.job,
                            error_message=exc.message,
                            category=JobFailureCategory.NON_RETRYABLE,
                        )
                        bound_log.error(
                            "reconciliation_job_failed_non_retryable",
                            error=exc.message,
                            duration_ms=int((time.monotonic() - start) * 1000),
                        )
                    except (RetryableJobError, ReconciliationJobError) as exc:
                        await mark_job_failed(
                            session,
                            claim.job,
                            error_message=exc.message,
                            category=JobFailureCategory.RETRYABLE,
                        )
                        await clear_enqueue_marker(self._redis, job_id)
                        await enqueue_reconciliation_job(self._redis, job_id, set_marker=True)
                        bound_log.warning(
                            "reconciliation_job_failed_retryable",
                            error=exc.message,
                            duration_ms=int((time.monotonic() - start) * 1000),
                        )
        except Exception as exc:
            bound_log.exception(
                "reconciliation_job_unhandled_error",
                error_type=type(exc).__name__,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
