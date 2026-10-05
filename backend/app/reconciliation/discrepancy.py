"""Reconciliation discrepancy record."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class DiscrepancySeverity(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class ReconDiscrepancy:
    """Detected ledger mismatch requiring investigation (maps to recon_discrepancies)."""

    id: uuid.UUID
    merchant_id: uuid.UUID
    severity: str
    description: str
    webhook_event_id: uuid.UUID | None = None
    dossier_path: str | None = None
    dossier_hash: str | None = None
    resolved_at: datetime | None = None
    detected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
