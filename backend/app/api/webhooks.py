"""Stripe webhook ingestion endpoint.

Security guarantees:
- Signature verified via stripe.Webhook.construct_event() before any processing.
- Raw payload read as bytes; never logged or stored.
- Idempotent: duplicate events return 200 immediately.
- Zero cardholder data in logs or storage.

Note:
    stripe-python exposes ``stripe.Webhook.construct_event`` (not
    ``WebhookClient``). Verified against stripe-python ≥11 docs / stripe 16.0.0.
"""

from __future__ import annotations

from datetime import UTC, datetime

import stripe
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.redis import get_redis
from app.models.webhook_event import WebhookEvent
from app.webhooks.deduplicator import WebhookDeduplicator
from app.webhooks.models import NormalizedWebhookEvent
from app.webhooks.normalizer import normalize_stripe_event

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = structlog.get_logger(__name__)

# Module-level dependency defaults (avoids ruff B008 on FastAPI Depends/Header).
_StripeSignatureHeader = Header(None, alias="stripe-signature")
_RedisDep = Depends(get_redis)
_DbDep = Depends(get_db)


@router.post(
    "/stripe",
    status_code=status.HTTP_200_OK,
    summary="Ingest Stripe webhook events",
    description=(
        "Accepts Stripe webhook POST requests. Verifies the Stripe-Signature header, "
        "deduplicates events via Redis, normalizes the payload (stripping cardholder data), "
        "and enqueues for reconciliation."
    ),
)
async def ingest_stripe_webhook(
    request: Request,
    stripe_signature: str | None = _StripeSignatureHeader,
    redis: Redis = _RedisDep,  # type: ignore[type-arg]
    db: AsyncSession = _DbDep,
) -> dict[str, str]:
    """Ingest and validate a Stripe webhook delivery.

    Returns:
        {"status": "accepted"} on success.
        {"status": "duplicate"} if already processed (idempotent 200).

    Raises:
        HTTPException 400: missing or invalid Stripe-Signature.
        HTTPException 400: payload construction failure.
    """
    if not stripe_signature:
        log.warning("webhook_missing_signature")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe-Signature header",
        )

    raw_body: bytes = await request.body()
    settings = get_settings()

    try:
        # Real stripe-python API: stripe.Webhook.construct_event(payload, sig_header, secret)
        event = stripe.Webhook.construct_event(
            payload=raw_body,
            sig_header=stripe_signature,
            secret=settings.stripe_webhook_secret,
        )
    except stripe.SignatureVerificationError as exc:
        log.warning("webhook_signature_invalid", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Stripe signature",
        ) from exc
    except Exception as exc:
        log.error("webhook_construct_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook payload error",
        ) from exc

    deduplicator = WebhookDeduplicator(redis)
    if await deduplicator.is_duplicate(event.id):
        return {"status": "duplicate"}

    normalized: NormalizedWebhookEvent = normalize_stripe_event(event)

    log.info(
        "webhook_accepted",
        stripe_event_id=normalized.stripe_event_id,
        event_type=normalized.event_type,
        object_id=normalized.object_id,
    )

    webhook_row = WebhookEvent(
        stripe_event_id=normalized.stripe_event_id,
        event_type=str(normalized.event_type),
        livemode=normalized.livemode,
        api_version=normalized.api_version,
        object_id=normalized.object_id,
        object_status=normalized.object_status,
        amount=normalized.amount,
        currency=normalized.currency,
        metadata=normalized.metadata,
        stripe_created_at=normalized.created_at,
        received_at=datetime.now(UTC),
    )
    try:
        db.add(webhook_row)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        log.warning("webhook_db_duplicate", stripe_event_id=normalized.stripe_event_id)
        return {"status": "duplicate"}

    return {"status": "accepted"}
