from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


class DossierSeverity(StrEnum):
    INFO = "info"  # Matched — no action needed
    WARNING = "warning"  # Probe-only or webhook-only
    CRITICAL = "critical"  # Discrepancy detected


@dataclass(frozen=True)
class DossierRequest:
    """Input to the PDF dossier builder."""

    merchant_id: uuid.UUID
    report_id: uuid.UUID = field(default_factory=uuid.uuid4)
    severity: DossierSeverity = DossierSeverity.INFO

    # Probe fields
    probe_id: str = ""
    probe_status: str = ""
    probe_duration_ms: float = 0.0
    probe_failure_mode: str = ""
    probe_amount: int = 0
    probe_currency: str = ""

    # Reconciliation fields
    reconciliation_status: str = ""
    discrepancies: list[str] = field(default_factory=list)
    webhook_amount: int | None = None
    webhook_currency: str | None = None

    # Snapshot chain (tamper-evidence linkage across dossiers)
    chain_seq: int | None = None
    prev_hash: str | None = None

    # Metadata
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class DossierResult:
    """Output from the PDF dossier builder."""

    report_id: uuid.UUID
    output_path: Path
    sha256_hash: str
    file_size_bytes: int
    generated_at: datetime
