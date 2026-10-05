from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class ReconciliationStatus(StrEnum):
    MATCHED = "matched"  # Probe and webhook agree
    PROBE_ONLY = "probe_only"  # Probe fired but no matching webhook arrived
    WEBHOOK_ONLY = "webhook_only"  # Webhook arrived but no probe record exists
    DISCREPANCY = "discrepancy"  # Both present but values differ


class DiscrepancyField(StrEnum):
    AMOUNT = "amount"
    CURRENCY = "currency"
    STATUS = "status"
    LIVEMODE = "livemode"


@dataclass(frozen=True)
class ReconciliationReport:
    """Immutable result of matching a probe record against a webhook event."""

    report_id: uuid.UUID = field(default_factory=uuid.uuid4)
    merchant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    probe_id: uuid.UUID | None = None
    stripe_event_id: str | None = None
    stripe_intent_id: str | None = None
    status: ReconciliationStatus = ReconciliationStatus.PROBE_ONLY
    discrepancies: list[DiscrepancyField] = field(default_factory=list)
    probe_amount: int | None = None
    webhook_amount: int | None = None
    probe_currency: str | None = None
    webhook_currency: str | None = None
    reconciled_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    window_seconds: int = 300

    @property
    def is_clean(self) -> bool:
        """True when probe and webhook agree with no discrepancies."""
        return self.status == ReconciliationStatus.MATCHED and not self.discrepancies
