"""Orchestrate incident persistence, evidence, and alerting after reconciliation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerting.dispatcher import AlertDispatcher
from app.artifacts.models import DossierRequest
from app.artifacts.pdf_builder import PdfDossierBuilder
from app.core.config import Settings, get_settings
from app.incidents.policy import dossier_severity, incident_severity, requires_incident
from app.persistence.models import (
    ProbeResultRow,
    ReconciliationJob,
    ReconDiscrepancy,
)
from app.reconciliation.models import ReconciliationReport

log = structlog.get_logger(__name__)


class IncidentService:
    """Persist incidents, generate dossiers, and dispatch alerts."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        dossier_builder: PdfDossierBuilder | None = None,
        alert_dispatcher: AlertDispatcher | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        artifact_dir = Path(self._settings.artifact_output_dir)
        self._dossier_builder = dossier_builder or PdfDossierBuilder(output_dir=artifact_dir)
        self._alert_dispatcher = alert_dispatcher or AlertDispatcher(settings=self._settings)

    async def handle_reconciliation_outcome(
        self,
        session: AsyncSession,
        job: ReconciliationJob,
        report: ReconciliationReport,
    ) -> ReconDiscrepancy | None:
        """Create or load incident, generate evidence, and attempt alert delivery."""
        if not requires_incident(report):
            return None

        incident = await self._upsert_incident(session, job, report)
        await session.flush()

        try:
            dossier_request = await self._build_dossier_request(session, job, report, incident)
            result = self._dossier_builder.build(dossier_request)
            incident.dossier_path = str(result.output_path)
            incident.dossier_sha256 = result.sha256_hash
            incident.manifest_path = str(result.manifest_path)
            incident.manifest_sha256 = result.manifest_sha256
            incident.dossier_error = None
            await session.flush()
        except Exception as exc:
            incident.dossier_error = str(exc)[:2000]
            await session.flush()
            log.exception(
                "incident_dossier_failed",
                incident_id=str(incident.id),
                job_id=str(job.id),
            )

        try:
            await self._alert_dispatcher.dispatch_incident(session, incident)
        except Exception:
            log.exception(
                "incident_alert_dispatch_failed",
                incident_id=str(incident.id),
            )

        return incident

    async def _upsert_incident(
        self,
        session: AsyncSession,
        job: ReconciliationJob,
        report: ReconciliationReport,
    ) -> ReconDiscrepancy:
        severity = incident_severity(report)
        discrepancy_type = report.status.value
        webhook_event_id = job.webhook_event_id

        stmt = insert(ReconDiscrepancy).values(
            id=uuid.uuid4(),
            merchant_id=job.merchant_id,
            reconciliation_job_id=job.id,
            webhook_event_id=webhook_event_id,
            probe_id=report.probe_id,
            payment_intent_id=report.stripe_intent_id,
            discrepancy_type=discrepancy_type,
            severity=severity,
            status="open",
            detected_at=report.reconciled_at,
        )
        stmt = stmt.on_conflict_do_nothing(index_elements=["reconciliation_job_id"])
        await session.execute(stmt)
        await session.flush()

        existing = (
            await session.execute(
                select(ReconDiscrepancy).where(
                    ReconDiscrepancy.reconciliation_job_id == job.id,
                )
            )
        ).scalar_one()
        return existing

    async def _build_dossier_request(
        self,
        session: AsyncSession,
        job: ReconciliationJob,
        report: ReconciliationReport,
        incident: ReconDiscrepancy,
    ) -> DossierRequest:
        probe_status = ""
        probe_duration_ms = 0.0
        probe_failure_mode = ""
        probe_id_str = ""
        probe_amount = report.probe_amount or 0
        probe_currency = report.probe_currency or ""

        if report.probe_id is not None:
            probe_row = await session.get(ProbeResultRow, report.probe_id)
            if probe_row is not None:
                probe_status = probe_row.status
                probe_id_str = str(probe_row.id)

        webhook_amount = report.webhook_amount
        webhook_currency = report.webhook_currency

        return DossierRequest(
            merchant_id=job.merchant_id,
            report_id=report.report_id,
            incident_id=incident.id,
            severity=dossier_severity(report),
            probe_id=probe_id_str,
            probe_status=probe_status,
            probe_duration_ms=probe_duration_ms,
            probe_failure_mode=probe_failure_mode,
            probe_amount=probe_amount,
            probe_currency=probe_currency,
            reconciliation_status=report.status.value,
            discrepancies=[d.value for d in report.discrepancies],
            webhook_amount=webhook_amount,
            webhook_currency=webhook_currency,
            generated_at=datetime.now(UTC),
        )
