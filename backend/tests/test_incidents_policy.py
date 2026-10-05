"""Unit tests for incident policy helpers."""

from __future__ import annotations

from app.artifacts.models import DossierSeverity
from app.incidents.policy import dossier_severity, incident_severity
from app.persistence.enums import IncidentSeverity
from app.reconciliation.models import ReconciliationReport, ReconciliationStatus


def test_incident_severity_mapping() -> None:
    assert (
        incident_severity(ReconciliationReport(status=ReconciliationStatus.DISCREPANCY))
        == IncidentSeverity.HIGH
    )
    assert (
        incident_severity(ReconciliationReport(status=ReconciliationStatus.PROBE_ONLY))
        == IncidentSeverity.MEDIUM
    )


def test_dossier_severity_mapping() -> None:
    assert (
        dossier_severity(ReconciliationReport(status=ReconciliationStatus.DISCREPANCY))
        == DossierSeverity.CRITICAL
    )
    assert (
        dossier_severity(ReconciliationReport(status=ReconciliationStatus.WEBHOOK_ONLY))
        == DossierSeverity.WARNING
    )
