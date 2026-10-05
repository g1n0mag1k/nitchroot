"""Stripe webhook payload normalizer.

Extracts only the fields SnitchRoot needs. Never stores or logs raw payloads.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import stripe
import structlog

from .models import NormalizedWebhookEvent, WebhookEventType

log = structlog.get_logger(__name__)

# Metadata keys containing these substrings are treated as PII and stripped.
_PII_KEY_SIGNALS = frozenset({"email", "phone", "name", "address", "card", "ssn", "dob"})


def normalize_stripe_event(event: stripe.Event) -> NormalizedWebhookEvent:
    """Convert a verified stripe.Event into a NormalizedWebhookEvent.

    Args:
        event: A stripe.Event object (already signature-verified).

    Returns:
        NormalizedWebhookEvent with cardholder data stripped.
    """
    try:
        event_type = WebhookEventType(event.type)
    except ValueError:
        event_type = WebhookEventType.UNKNOWN

    obj = event.data.object  # PaymentIntent, Charge, Payout, etc.

    # Extract only safe, non-PII fields
    amount = getattr(obj, "amount", 0) or 0
    currency = getattr(obj, "currency", "") or ""
    object_status = getattr(obj, "status", "") or ""
    raw_metadata: dict[str, Any] = getattr(obj, "metadata", {}) or {}

    # Coerce metadata values to strings, exclude any key containing PII signals
    safe_metadata = {
        k: str(v)
        for k, v in raw_metadata.items()
        if not any(sig in k.lower() for sig in _PII_KEY_SIGNALS)
    }

    normalized = NormalizedWebhookEvent(
        stripe_event_id=event.id,
        event_type=event_type,
        livemode=bool(event.livemode),
        api_version=event.api_version or "",
        created_at=datetime.fromtimestamp(event.created, tz=UTC),
        object_id=obj.id,
        object_status=object_status,
        amount=amount,
        currency=currency,
        metadata=safe_metadata,
    )

    log.info(
        "webhook_event_normalized",
        stripe_event_id=normalized.stripe_event_id,
        event_type=normalized.event_type,
        livemode=normalized.livemode,
    )
    return normalized
