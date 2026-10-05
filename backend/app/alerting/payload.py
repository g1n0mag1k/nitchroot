"""Safe alert payload construction — no raw Stripe data or secrets."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.persistence.enums import IncidentSeverity
from app.persistence.models import ReconDiscrepancy


@dataclass(frozen=True)
class IncidentAlertPayload:
    """Operational fields safe for webhook consumers."""

    incident_id: uuid.UUID
    merchant_id: uuid.UUID
    discrepancy_type: str
    severity: IncidentSeverity
    status: str
    detected_at: datetime
    payment_intent_id: str | None
    reconciliation_job_id: uuid.UUID
    dossier_filename: str | None
    dossier_sha256: str | None
    manifest_filename: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "incident_id": str(self.incident_id),
            "merchant_id": str(self.merchant_id),
            "discrepancy_type": self.discrepancy_type,
            "severity": self.severity.value,
            "status": self.status,
            "detected_at": self.detected_at.isoformat(),
            "payment_intent_id": self.payment_intent_id,
            "reconciliation_job_id": str(self.reconciliation_job_id),
            "dossier_filename": self.dossier_filename,
            "dossier_sha256": self.dossier_sha256,
            "manifest_filename": self.manifest_filename,
        }


def build_alert_payload(incident: ReconDiscrepancy) -> IncidentAlertPayload:
    dossier_filename = None
    manifest_filename = None
    if incident.dossier_path:
        dossier_filename = incident.dossier_path.rsplit("/", 1)[-1]
    if incident.manifest_path:
        manifest_filename = incident.manifest_path.rsplit("/", 1)[-1]

    return IncidentAlertPayload(
        incident_id=incident.id,
        merchant_id=incident.merchant_id,
        discrepancy_type=incident.discrepancy_type,
        severity=incident.severity,
        status=incident.status,
        detected_at=incident.detected_at,
        payment_intent_id=incident.payment_intent_id,
        reconciliation_job_id=incident.reconciliation_job_id,
        dossier_filename=dossier_filename,
        dossier_sha256=incident.dossier_sha256,
        manifest_filename=manifest_filename,
    )
