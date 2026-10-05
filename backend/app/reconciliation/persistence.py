"""Persist reconciliation domain objects to the database."""

from __future__ import annotations

from app.models.recon_discrepancy import ReconDiscrepancy as ReconDiscrepancyRow
from app.reconciliation.models import ReconDiscrepancy as ReconDiscrepancyDTO


def discrepancy_dto_to_row(dto: ReconDiscrepancyDTO) -> ReconDiscrepancyRow:
    """Map an in-memory discrepancy to an ORM row."""
    severity = dto.severity.value if hasattr(dto.severity, "value") else str(dto.severity)
    return ReconDiscrepancyRow(
        id=dto.discrepancy_id,
        merchant_id=dto.merchant_id,
        webhook_event_id=dto.webhook_event_id,
        stripe_event_id=dto.stripe_event_id,
        severity=severity,
        description=dto.description,
        probe_id=dto.probe_id,
        stripe_intent_id=dto.stripe_intent_id,
        detected_at=dto.detected_at,
    )
