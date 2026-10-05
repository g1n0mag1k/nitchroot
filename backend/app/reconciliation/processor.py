"""Execute a single reconciliation job via the existing ReconciliationEngine."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import ProbeResultRow, ReconciliationJob, WebhookEventRow
from app.probes.models import ProbeStatus
from app.reconciliation.engine import ReconciliationEngine
from app.reconciliation.exceptions import NonRetryableJobError, RetryableJobError
from app.reconciliation.mappers import probe_row_to_domain, webhook_row_to_normalized
from app.reconciliation.models import ReconciliationReport

log = structlog.get_logger(__name__)


async def _load_matching_probe(
    session: AsyncSession,
    job: ReconciliationJob,
    webhook: WebhookEventRow,
) -> ProbeResultRow | None:
    """Find the best probe candidate for this webhook (by PaymentIntent id)."""
    if not webhook.object_id:
        return None

    stmt = (
        select(ProbeResultRow)
        .where(
            ProbeResultRow.merchant_id == job.merchant_id,
            ProbeResultRow.status == ProbeStatus.SUCCEEDED.value,
            ProbeResultRow.stripe_intent_id == webhook.object_id,
        )
        .order_by(ProbeResultRow.probed_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def process_reconciliation_job(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    engine: ReconciliationEngine | None = None,
) -> ReconciliationReport:
    """Load durable job state, reconcile, and return the engine report.

    Does not mutate job status — the worker owns lifecycle transitions.
    """
    engine = engine or ReconciliationEngine()
    job = await session.get(ReconciliationJob, job_id)
    if job is None:
        raise NonRetryableJobError(f"reconciliation job not found: {job_id}")

    webhook_row = await session.get(WebhookEventRow, job.webhook_event_id)
    if webhook_row is None:
        raise NonRetryableJobError(f"webhook missing for job {job_id}")

    webhook = webhook_row_to_normalized(webhook_row)
    probe_row = await _load_matching_probe(session, job, webhook_row)
    probe = probe_row_to_domain(probe_row) if probe_row is not None else None

    try:
        report = engine.reconcile(probe, webhook)
    except ValueError as exc:
        raise NonRetryableJobError(str(exc)) from exc
    except Exception as exc:
        raise RetryableJobError("reconciliation engine failure") from exc

    log.info(
        "reconciliation_job_processed",
        job_id=str(job_id),
        merchant_id=str(job.merchant_id),
        status=report.status.value,
    )
    return report
