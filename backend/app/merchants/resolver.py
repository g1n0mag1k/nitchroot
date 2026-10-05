"""Resolve SnitchRoot merchant identity from normalized webhook data.

Stripe webhooks do not carry a first-class tenant identifier. The only
supported association in this codebase is the probe-injected PaymentIntent
metadata field ``merchant_id``, which must be a UUID string matching a row
in ``merchants``. We never infer tenant identity from other metadata keys.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.merchant import Merchant
from app.webhooks.models import NormalizedWebhookEvent

log = structlog.get_logger(__name__)

_METADATA_MERCHANT_KEY = "merchant_id"


class MerchantResolutionError(Exception):
    """Raised when a webhook cannot be mapped to a known merchant."""


async def resolve_merchant_for_webhook(
    session: AsyncSession,
    event: NormalizedWebhookEvent,
) -> Merchant:
    """Look up the merchant for a normalized webhook event.

    Args:
        session: Active database session.
        event: Normalized webhook payload (post-signature-verification).

    Returns:
        The matching Merchant row.

    Raises:
        MerchantResolutionError: If metadata is missing or does not reference
            a known merchant UUID.
    """
    raw_merchant_id = event.metadata.get(_METADATA_MERCHANT_KEY)
    if not raw_merchant_id:
        log.warning(
            "merchant_resolution_missing_metadata",
            stripe_event_id=event.stripe_event_id,
            metadata_keys=sorted(event.metadata.keys()),
        )
        raise MerchantResolutionError("Missing merchant_id in webhook metadata")

    try:
        merchant_uuid = uuid.UUID(str(raw_merchant_id))
    except ValueError:
        log.warning(
            "merchant_resolution_invalid_uuid",
            stripe_event_id=event.stripe_event_id,
        )
        raise MerchantResolutionError("Invalid merchant_id in webhook metadata") from None

    result = await session.execute(select(Merchant).where(Merchant.id == merchant_uuid))
    merchant = result.scalar_one_or_none()
    if merchant is None:
        log.warning(
            "merchant_resolution_unknown_merchant",
            stripe_event_id=event.stripe_event_id,
            merchant_id=str(merchant_uuid),
        )
        raise MerchantResolutionError("Unknown merchant_id in webhook metadata")

    return merchant
