"""Durable reconciliation job lifecycle helpers (PostgreSQL source of truth)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.persistence.enums import JobFailureCategory, ReconciliationJobStatus
from app.persistence.models import ReconciliationJob, ReconciliationResultRow
from app.reconciliation.models import ReconciliationReport

log = structlog.get_logger(__name__)


class JobClaimResult:
    """Outcome of attempting to claim a job for processing."""

    __slots__ = ("job", "reason")

    def __init__(self, job: ReconciliationJob | None, reason: str) -> None:
        self.job = job
        self.reason = reason


async def claim_reconciliation_job(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    settings: Settings | None = None,
) -> JobClaimResult:
    """Atomically claim a job using row-level locking.

    Skips jobs already completed or actively processed by another worker.
    Reclaims stale PROCESSING jobs whose lease has expired.
    """
    settings = settings or get_settings()
    lease = timedelta(seconds=settings.recon_processing_timeout_seconds)
    now = datetime.now(tz=UTC)

    async with session.begin_nested():
        stmt = (
            select(ReconciliationJob)
            .where(ReconciliationJob.id == job_id)
            .with_for_update(skip_locked=True)
        )
        job = (await session.execute(stmt)).scalar_one_or_none()
        if job is None:
            return JobClaimResult(None, "not_found_or_locked")

        if job.status == ReconciliationJobStatus.COMPLETED:
            return JobClaimResult(job, "already_completed")

        if job.status == ReconciliationJobStatus.PROCESSING:
            started = job.processing_started_at
            if started is not None and now - started < lease:
                return JobClaimResult(job, "already_processing")

        if job.status == ReconciliationJobStatus.FAILED:
            if job.failure_category == JobFailureCategory.NON_RETRYABLE:
                return JobClaimResult(job, "failed_non_retryable")
            if job.attempt_count >= job.max_attempts:
                return JobClaimResult(job, "failed_max_attempts")

        job.status = ReconciliationJobStatus.PROCESSING
        job.processing_started_at = now
        job.attempt_count += 1
        job.updated_at = now
        await session.flush()
        return JobClaimResult(job, "claimed")


async def mark_job_completed(
    session: AsyncSession,
    job: ReconciliationJob,
    report: ReconciliationReport,
) -> ReconciliationResultRow:
    """Persist reconciliation outcome and mark the job completed (idempotent)."""
    now = datetime.now(tz=UTC)
    existing = (
        await session.execute(
            select(ReconciliationResultRow).where(ReconciliationResultRow.job_id == job.id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        job.status = ReconciliationJobStatus.COMPLETED
        job.completed_at = job.completed_at or now
        job.updated_at = now
        job.last_error = None
        job.failure_category = None
        await session.flush()
        return existing

    result_row = ReconciliationResultRow(
        job_id=job.id,
        merchant_id=job.merchant_id,
        status=report.status,
        probe_id=report.probe_id,
        stripe_event_id=report.stripe_event_id,
        stripe_intent_id=report.stripe_intent_id,
        discrepancies=[d.value for d in report.discrepancies],
        probe_amount=report.probe_amount,
        webhook_amount=report.webhook_amount,
        probe_currency=report.probe_currency,
        webhook_currency=report.webhook_currency,
        window_seconds=report.window_seconds,
        reconciled_at=report.reconciled_at,
    )
    session.add(result_row)
    job.status = ReconciliationJobStatus.COMPLETED
    job.completed_at = now
    job.updated_at = now
    job.last_error = None
    job.failure_category = None
    await session.flush()
    return result_row


async def mark_job_failed(
    session: AsyncSession,
    job: ReconciliationJob,
    *,
    error_message: str,
    category: JobFailureCategory,
) -> None:
    """Record failure metadata and set durable job status."""
    now = datetime.now(tz=UTC)
    job.last_error = error_message[:2000]
    job.failure_category = category
    job.updated_at = now
    job.processing_started_at = None

    if category == JobFailureCategory.RETRYABLE and job.attempt_count < job.max_attempts:
        job.status = ReconciliationJobStatus.PENDING
    else:
        job.status = ReconciliationJobStatus.FAILED
    await session.flush()


async def recover_stale_processing_jobs(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
) -> int:
    """Move expired PROCESSING jobs back to PENDING for worker retry."""
    settings = settings or get_settings()
    cutoff = datetime.now(tz=UTC) - timedelta(seconds=settings.recon_processing_timeout_seconds)
    stmt = (
        update(ReconciliationJob)
        .where(
            ReconciliationJob.status == ReconciliationJobStatus.PROCESSING,
            ReconciliationJob.processing_started_at.is_not(None),
            ReconciliationJob.processing_started_at < cutoff,
        )
        .values(
            status=ReconciliationJobStatus.PENDING,
            processing_started_at=None,
            updated_at=datetime.now(tz=UTC),
        )
        .returning(ReconciliationJob.id)
    )
    result = await session.execute(stmt)
    recovered = len(result.fetchall())
    if recovered:
        log.warning("reconciliation_jobs_stale_recovered", count=recovered)
    return recovered


async def list_pending_jobs(
    session: AsyncSession,
    *,
    limit: int = 100,
) -> list[ReconciliationJob]:
    """Return pending jobs eligible for scheduler enqueue."""
    stmt = (
        select(ReconciliationJob)
        .where(ReconciliationJob.status == ReconciliationJobStatus.PENDING)
        .order_by(ReconciliationJob.created_at.asc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())
