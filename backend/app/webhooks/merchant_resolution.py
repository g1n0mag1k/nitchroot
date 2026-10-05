"""Merchant resolution for ingested webhook events."""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import Merchant
from app.webhooks.models import NormalizedWebhookEvent

log = structlog.get_logger(__name__)


async def resolve_merchant_for_webhook(
    session: AsyncSession,
    event: NormalizedWebhookEvent,
) -> Merchant:
    """Resolve or create the merchant associated with a normalized webhook."""
    raw_ref = event.metadata.get("merchant_id", "").strip()
    merchant: Merchant | None = None

    if raw_ref:
        try:
            merchant_uuid = uuid.UUID(raw_ref)
        except ValueError:
            merchant_uuid = None

        if merchant_uuid is not None:
            merchant = await session.get(Merchant, merchant_uuid)

        if merchant is None:
            stmt = select(Merchant).where(Merchant.external_ref == raw_ref)
            merchant = (await session.execute(stmt)).scalar_one_or_none()

    if merchant is None:
        merchant_id = uuid.uuid4()
        if raw_ref:
            try:
                merchant_id = uuid.UUID(raw_ref)
            except ValueError:
                pass
        merchant = Merchant(id=merchant_id, name=raw_ref or "unknown", external_ref=raw_ref or None)
        session.add(merchant)
        await session.flush()
        log.info(
            "merchant_created_from_webhook",
            merchant_id=str(merchant.id),
            external_ref=raw_ref or None,
        )

    return merchant
