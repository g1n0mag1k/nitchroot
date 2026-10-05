"""Persist normalized webhook events and enqueue reconciliation work."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.merchants.resolver import resolve_merchant_for_webhook
from app.models.reconciliation import ReconciliationJob
from app.models.webhook_event import WebhookEventRecord
from app.reconciliation.queue import ReconciliationJobQueue
from app.webhooks.models import NormalizedWebhookEvent

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class WebhookPersistResult:
    """Outcome of persisting a normalized webhook event."""

    webhook_event_id: uuid.UUID
    reconciliation_job_id: uuid.UUID
    created: bool


class WebhookPersistenceService:
    """Stores normalized events and creates idempotent reconciliation jobs."""

    def __init__(self, session: AsyncSession, redis: Redis) -> None:  # type: ignore[type-arg]
        self._session = session
        self._queue = ReconciliationJobQueue(redis)

    async def persist_and_enqueue(
        self,
        event: NormalizedWebhookEvent,
    ) -> WebhookPersistResult:
        """Resolve merchant, persist webhook, and enqueue reconciliation.

        Args:
            event: Sanitized webhook event (never raw Stripe payload).

        Returns:
            WebhookPersistResult with IDs and whether a new row was created.

        Raises:
            MerchantResolutionError: When merchant cannot be resolved safely.
        """
        merchant = await resolve_merchant_for_webhook(self._session, event)

        existing = await self._session.execute(
            select(WebhookEventRecord).where(
                WebhookEventRecord.stripe_event_id == event.stripe_event_id
            )
        )
        existing_row = existing.scalar_one_or_none()
        if existing_row is not None:
            job = await self._get_or_create_job_for_webhook(
                merchant_id=merchant.id,
                webhook_event_id=existing_row.id,
                stripe_event_id=event.stripe_event_id,
            )
            return WebhookPersistResult(
                webhook_event_id=existing_row.id,
                reconciliation_job_id=job.id,
                created=False,
            )

        row = WebhookEventRecord(
            id=event.internal_id,
            merchant_id=merchant.id,
            stripe_event_id=event.stripe_event_id,
            event_type=str(event.event_type),
            payment_intent_id=event.object_id or None,
            amount=event.amount,
            currency=event.currency,
            object_status=event.object_status,
            livemode=event.livemode,
            api_version=event.api_version,
            stripe_created_at=event.created_at,
            metadata_json=dict(event.metadata),
        )
        self._session.add(row)
        try:
            async with self._session.begin_nested():
                await self._session.flush()
        except IntegrityError:
            log.info(
                "webhook_persist_duplicate_race",
                stripe_event_id=event.stripe_event_id,
            )
            dup = await self._session.execute(
                select(WebhookEventRecord).where(
                    WebhookEventRecord.stripe_event_id == event.stripe_event_id
                )
            )
            existing_row = dup.scalar_one()
            job = await self._get_or_create_job_for_webhook(
                merchant_id=merchant.id,
                webhook_event_id=existing_row.id,
                stripe_event_id=event.stripe_event_id,
            )
            return WebhookPersistResult(
                webhook_event_id=existing_row.id,
                reconciliation_job_id=job.id,
                created=False,
            )

        job = await self._create_job(
            merchant_id=merchant.id,
            webhook_event_id=row.id,
            stripe_event_id=event.stripe_event_id,
        )

        log.info(
            "webhook_persisted",
            stripe_event_id=event.stripe_event_id,
            webhook_event_id=str(row.id),
            reconciliation_job_id=str(job.id),
        )
        return WebhookPersistResult(
            webhook_event_id=row.id,
            reconciliation_job_id=job.id,
            created=True,
        )

    async def _get_or_create_job_for_webhook(
        self,
        merchant_id: uuid.UUID,
        webhook_event_id: uuid.UUID,
        stripe_event_id: str,
    ) -> ReconciliationJob:
        result = await self._session.execute(
            select(ReconciliationJob).where(ReconciliationJob.webhook_event_id == webhook_event_id)
        )
        job = result.scalar_one_or_none()
        if job is not None:
            return job
        return await self._create_job(merchant_id, webhook_event_id, stripe_event_id)

    async def _create_job(
        self,
        merchant_id: uuid.UUID,
        webhook_event_id: uuid.UUID,
        stripe_event_id: str,
    ) -> ReconciliationJob:
        job = ReconciliationJob(
            merchant_id=merchant_id,
            webhook_event_id=webhook_event_id,
            stripe_event_id=stripe_event_id,
            status="pending",
        )
        self._session.add(job)
        try:
            async with self._session.begin_nested():
                await self._session.flush()
        except IntegrityError:
            result = await self._session.execute(
                select(ReconciliationJob).where(
                    ReconciliationJob.stripe_event_id == stripe_event_id
                )
            )
            return result.scalar_one()
        await self._queue.enqueue(job.id)
        return job
