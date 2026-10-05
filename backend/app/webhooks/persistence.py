"""Map normalized webhook events to ORM rows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog

from app.models.webhook_event import WebhookEvent
from app.webhooks.models import NormalizedWebhookEvent, WebhookEventType

log = structlog.get_logger(__name__)

PERSISTABLE_EVENT_TYPES: frozenset[WebhookEventType] = frozenset(
    {
        WebhookEventType.PAYMENT_INTENT_SUCCEEDED,
        WebhookEventType.PAYMENT_INTENT_PAYMENT_FAILED,
        WebhookEventType.CHARGE_SUCCEEDED,
        WebhookEventType.CHARGE_FAILED,
    }
)

_PROBE_MATCH_EVENT_TYPES: frozenset[WebhookEventType] = frozenset(
    {
        WebhookEventType.PAYMENT_INTENT_SUCCEEDED,
        WebhookEventType.PAYMENT_INTENT_PAYMENT_FAILED,
    }
)


def merchant_id_from_metadata(metadata: dict[str, str]) -> uuid.UUID | None:
    """Parse merchant UUID from normalized webhook metadata."""
    raw = metadata.get("merchant_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        return None


def payment_intent_id_for_event(normalized: NormalizedWebhookEvent) -> str | None:
    """Return the Stripe PaymentIntent id used for probe correlation."""
    if normalized.event_type in _PROBE_MATCH_EVENT_TYPES:
        return normalized.object_id or None
    if normalized.object_id.startswith("pi_"):
        return normalized.object_id
    return None


def should_persist_event(normalized: NormalizedWebhookEvent) -> bool:
    """True when this event type should be written to ``webhook_events``."""
    return normalized.event_type in PERSISTABLE_EVENT_TYPES


def webhook_event_from_normalized(
    normalized: NormalizedWebhookEvent,
    *,
    merchant_id: uuid.UUID,
    received_at: datetime | None = None,
) -> WebhookEvent:
    """Build a ``WebhookEvent`` ORM instance from a normalized payload."""
    return WebhookEvent(
        id=normalized.internal_id,
        merchant_id=merchant_id,
        stripe_event_id=normalized.stripe_event_id,
        event_type=normalized.event_type.value,
        payment_intent_id=payment_intent_id_for_event(normalized),
        amount=normalized.amount,
        currency=normalized.currency,
        livemode=normalized.livemode,
        api_version=normalized.api_version,
        object_status=normalized.object_status,
        stripe_created_at=normalized.created_at,
        received_at=received_at or datetime.now(UTC),
        metadata_json=dict(normalized.metadata),
    )
