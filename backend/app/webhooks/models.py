"""Webhook domain models — sanitized, CHD-free event representations."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class WebhookEventType(StrEnum):
    """Stripe webhook event types that SnitchRoot ingests."""

    PAYMENT_INTENT_SUCCEEDED = "payment_intent.succeeded"
    PAYMENT_INTENT_PAYMENT_FAILED = "payment_intent.payment_failed"
    PAYMENT_INTENT_CANCELED = "payment_intent.canceled"
    CHARGE_SUCCEEDED = "charge.succeeded"
    CHARGE_FAILED = "charge.failed"
    CHARGE_REFUNDED = "charge.refunded"
    PAYOUT_PAID = "payout.paid"
    PAYOUT_FAILED = "payout.failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class NormalizedWebhookEvent:
    """Sanitized, cardholder-data-free representation of a Stripe webhook event."""

    internal_id: uuid.UUID = field(default_factory=uuid.uuid4)
    stripe_event_id: str = ""  # e.g. "evt_..."
    event_type: WebhookEventType = WebhookEventType.UNKNOWN
    livemode: bool = False
    api_version: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Object-level fields — never raw payload
    object_id: str = ""  # PaymentIntent/Charge/Payout ID
    object_status: str = ""
    amount: int = 0  # in cents
    currency: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
