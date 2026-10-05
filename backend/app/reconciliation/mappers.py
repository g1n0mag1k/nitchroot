"""Map ORM rows to reconciliation domain models."""

from __future__ import annotations

from app.persistence.models import ProbeResultRow, WebhookEventRow
from app.probes.models import ProbeResult, ProbeStatus
from app.webhooks.models import NormalizedWebhookEvent, WebhookEventType


def webhook_row_to_normalized(row: WebhookEventRow) -> NormalizedWebhookEvent:
    """Convert a persisted webhook row into a NormalizedWebhookEvent."""
    try:
        event_type = WebhookEventType(row.event_type)
    except ValueError:
        event_type = WebhookEventType.UNKNOWN

    metadata = {str(k): str(v) for k, v in (row.metadata_json or {}).items()}
    return NormalizedWebhookEvent(
        internal_id=row.id,
        stripe_event_id=row.stripe_event_id,
        event_type=event_type,
        livemode=row.livemode,
        api_version=row.api_version,
        created_at=row.stripe_created_at,
        object_id=row.object_id,
        object_status=row.object_status,
        amount=row.amount,
        currency=row.currency,
        metadata=metadata,
    )


def probe_row_to_domain(row: ProbeResultRow) -> ProbeResult:
    """Convert a persisted probe row into a ProbeResult."""
    return ProbeResult(
        probe_id=row.id,
        merchant_id=row.merchant_id,
        status=ProbeStatus(row.status),
        latency_ms=0,
        probed_at=row.probed_at,
        stripe_intent_id=row.stripe_intent_id,
        amount=row.amount,
        currency=row.currency,
        idempotency_key=row.idempotency_key,
    )
