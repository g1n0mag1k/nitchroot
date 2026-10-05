"""Process reconciliation jobs using the existing ReconciliationEngine.

Workers (or integration tests) dequeue job IDs from Redis and call
``process_reconciliation_job`` to correlate webhook rows with probe records.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.probe_result import ProbeResultRecord
from app.models.reconciliation import ReconciliationJob, ReconciliationReportRecord
from app.models.webhook_event import WebhookEventRecord
from app.probes.models import ProbeResult, ProbeStatus
from app.reconciliation.engine import ReconciliationEngine
from app.reconciliation.models import ReconciliationReport
from app.webhooks.models import NormalizedWebhookEvent, WebhookEventType

log = structlog.get_logger(__name__)


def _webhook_row_to_domain(row: WebhookEventRecord) -> NormalizedWebhookEvent:
    try:
        event_type = WebhookEventType(row.event_type)
    except ValueError:
        event_type = WebhookEventType.UNKNOWN
    return NormalizedWebhookEvent(
        internal_id=row.id,
        stripe_event_id=row.stripe_event_id,
        event_type=event_type,
        livemode=row.livemode,
        api_version=row.api_version,
        created_at=row.stripe_created_at,
        object_id=row.payment_intent_id or "",
        object_status=row.object_status,
        amount=row.amount,
        currency=row.currency,
        metadata=dict(row.metadata_json),
    )


def _probe_row_to_domain(row: ProbeResultRecord) -> ProbeResult:
    return ProbeResult(
        probe_id=row.probe_id,
        merchant_id=row.merchant_id,
        status=ProbeStatus(row.status),
        latency_ms=row.latency_ms,
        probed_at=row.probed_at,
        stripe_intent_id=row.stripe_intent_id,
        amount=row.amount,
        currency=row.currency,
        idempotency_key=row.idempotency_key,
        raw_response_hash=row.raw_response_hash,
    )


async def process_reconciliation_job(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    window_seconds: int = 300,
) -> ReconciliationReport:
    """Run ReconciliationEngine for a queued job and persist the report.

    Args:
        session: Database session (caller commits).
        job_id: Primary key of ``reconciliation_jobs``.
        window_seconds: Sliding window passed to ReconciliationEngine.

    Returns:
        The in-memory ReconciliationReport from the engine.
    """
    job_result = await session.execute(
        select(ReconciliationJob).where(ReconciliationJob.id == job_id)
    )
    job = job_result.scalar_one()

    if job.status == "completed":
        existing = await session.execute(
            select(ReconciliationReportRecord).where(
                ReconciliationReportRecord.reconciliation_job_id == job.id
            )
        )
        record = existing.scalar_one()
        return _report_record_to_domain(record)

    webhook_result = await session.execute(
        select(WebhookEventRecord).where(WebhookEventRecord.id == job.webhook_event_id)
    )
    webhook_row = webhook_result.scalar_one()
    webhook = _webhook_row_to_domain(webhook_row)

    probe: ProbeResult | None = None
    if webhook.object_id:
        probe_result = await session.execute(
            select(ProbeResultRecord)
            .where(ProbeResultRecord.merchant_id == job.merchant_id)
            .where(ProbeResultRecord.stripe_intent_id == webhook.object_id)
            .order_by(ProbeResultRecord.probed_at.desc())
            .limit(1)
        )
        probe_row = probe_result.scalar_one_or_none()
        if probe_row is not None:
            probe = _probe_row_to_domain(probe_row)

    engine = ReconciliationEngine(window_seconds=window_seconds)
    report = engine.reconcile(probe, webhook)

    record = ReconciliationReportRecord(
        reconciliation_job_id=job.id,
        merchant_id=report.merchant_id,
        report_id=report.report_id,
        status=str(report.status),
        discrepancies=[str(d) for d in report.discrepancies],
        stripe_event_id=report.stripe_event_id,
        stripe_intent_id=report.stripe_intent_id,
        probe_id=report.probe_id,
        probe_amount=report.probe_amount,
        webhook_amount=report.webhook_amount,
        probe_currency=report.probe_currency,
        webhook_currency=report.webhook_currency,
        window_seconds=report.window_seconds,
        report_json={
            "status": str(report.status),
            "discrepancies": [str(d) for d in report.discrepancies],
        },
    )
    session.add(record)
    job.status = "completed"
    job.completed_at = datetime.now(UTC)

    log.info(
        "reconciliation_job_completed",
        job_id=str(job.id),
        status=report.status,
    )
    return report


def _report_record_to_domain(record: ReconciliationReportRecord) -> ReconciliationReport:
    from app.reconciliation.models import DiscrepancyField, ReconciliationStatus

    discrepancies = [DiscrepancyField(d) for d in record.discrepancies]
    return ReconciliationReport(
        report_id=record.report_id,
        merchant_id=record.merchant_id,
        probe_id=record.probe_id,
        stripe_event_id=record.stripe_event_id,
        stripe_intent_id=record.stripe_intent_id,
        status=ReconciliationStatus(record.status),
        discrepancies=discrepancies,
        probe_amount=record.probe_amount,
        webhook_amount=record.webhook_amount,
        probe_currency=record.probe_currency,
        webhook_currency=record.webhook_currency,
        window_seconds=record.window_seconds,
    )
