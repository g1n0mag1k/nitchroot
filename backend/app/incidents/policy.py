"""Incident eligibility rules derived from reconciliation semantics.

Decision (documented):
- MATCHED: no incident — probe and webhook agree.
- DISCREPANCY: incident with HIGH severity — material field mismatch.
- PROBE_ONLY / WEBHOOK_ONLY: incident with MEDIUM severity — correlation gaps
  that may indicate missing webhooks or probe coverage (see DossierSeverity.WARNING).
"""

from __future__ import annotations

from app.artifacts.models import DossierSeverity
from app.persistence.enums import IncidentSeverity
from app.reconciliation.models import ReconciliationReport, ReconciliationStatus


def requires_incident(report: ReconciliationReport) -> bool:
    """Return True when the reconciliation outcome should be persisted as an incident."""
    return report.status != ReconciliationStatus.MATCHED


def incident_severity(report: ReconciliationReport) -> IncidentSeverity:
    if report.status == ReconciliationStatus.DISCREPANCY:
        return IncidentSeverity.HIGH
    return IncidentSeverity.MEDIUM


def dossier_severity(report: ReconciliationReport) -> DossierSeverity:
    if report.status == ReconciliationStatus.DISCREPANCY:
        return DossierSeverity.CRITICAL
    return DossierSeverity.WARNING
