"""Persist normalized webhooks and enqueue durable reconciliation jobs."""

from __future__ import annotations

import uuid

import redis.asyncio as aioredis
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.enums import ReconciliationJobStatus
from app.persistence.models import ReconciliationJob, WebhookEventRow
from app.reconciliation.queue import enqueue_reconciliation_job
from app.webhooks.merchant_resolution import resolve_merchant_for_webhook
from app.webhooks.models import NormalizedWebhookEvent

log = structlog.get_logger(__name__)


async def persist_webhook_and_enqueue_reconciliation(
    session: AsyncSession,
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
    event: NormalizedWebhookEvent,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Persist webhook, create reconciliation job, enqueue job id in Redis.

    Returns:
        Tuple of (webhook_row_id, reconciliation_job_id).

    Raises:
        sqlalchemy.exc.IntegrityError: If stripe_event_id already exists (idempotent path).
    """
    existing = (
        await session.execute(
            select(WebhookEventRow).where(WebhookEventRow.stripe_event_id == event.stripe_event_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        job = (
            await session.execute(
                select(ReconciliationJob).where(ReconciliationJob.webhook_event_id == existing.id)
            )
        ).scalar_one_or_none()
        if job is None:
            raise RuntimeError("webhook persisted without reconciliation job")
        await enqueue_reconciliation_job(redis_client, job.id, set_marker=True)
        log.info(
            "webhook_persist_idempotent",
            stripe_event_id=event.stripe_event_id,
            job_id=str(job.id),
        )
        return existing.id, job.id

    merchant = await resolve_merchant_for_webhook(session, event)
    row = WebhookEventRow(
        id=event.internal_id,
        merchant_id=merchant.id,
        stripe_event_id=event.stripe_event_id,
        event_type=event.event_type.value,
        livemode=event.livemode,
        api_version=event.api_version,
        object_id=event.object_id,
        object_status=event.object_status,
        amount=event.amount,
        currency=event.currency,
        metadata_json=dict(event.metadata),
        stripe_created_at=event.created_at,
    )
    session.add(row)
    await session.flush()

    job = ReconciliationJob(
        merchant_id=merchant.id,
        webhook_event_id=row.id,
        status=ReconciliationJobStatus.PENDING,
    )
    session.add(job)
    await session.flush()

    await enqueue_reconciliation_job(redis_client, job.id, set_marker=True)
    log.info(
        "webhook_persisted_reconciliation_job_created",
        stripe_event_id=event.stripe_event_id,
        merchant_id=str(merchant.id),
        job_id=str(job.id),
    )
    return row.id, job.id
