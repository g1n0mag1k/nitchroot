"""Build signed dossiers for high-severity discrepancies and extend the snapshot chain."""

from __future__ import annotations

import uuid

import redis.asyncio as aioredis
import structlog

from app.artifacts.chain import SnapshotChain
from app.artifacts.models import DossierRequest, DossierSeverity
from app.artifacts.pdf_builder import PdfDossierBuilder
from app.probes.models import ProbeResult
from app.reconciliation.discrepancy import DiscrepancySeverity, ReconDiscrepancy
from app.reconciliation.models import ReconciliationReport
from app.webhooks.models import NormalizedWebhookEvent

log = structlog.get_logger(__name__)


def _severity_for_report(report: ReconciliationReport) -> DiscrepancySeverity:
    if report.discrepancies:
        return DiscrepancySeverity.HIGH
    return DiscrepancySeverity.LOW


def _dossier_severity(report: ReconciliationReport) -> DossierSeverity:
    if report.discrepancies:
        return DossierSeverity.CRITICAL
    return DossierSeverity.WARNING


async def build_dossier_for_discrepancy(
    discrepancy: ReconDiscrepancy,
    report: ReconciliationReport,
    probe: ProbeResult,
    webhook: NormalizedWebhookEvent | None,
    redis_client: aioredis.Redis,  # type: ignore[type-arg]
    builder: PdfDossierBuilder | None = None,
) -> ReconDiscrepancy:
    """Render a PDF dossier for HIGH discrepancies and append the merchant snapshot chain."""
    if discrepancy.severity != DiscrepancySeverity.HIGH:
        return discrepancy

    chain = SnapshotChain(redis_client)
    existing = await chain.export(discrepancy.merchant_id)
    chain_seq = len(existing)
    prev_hash = existing[-1].dossier_hash if existing else "GENESIS"

    pdf_builder = builder or PdfDossierBuilder()
    request = DossierRequest(
        merchant_id=discrepancy.merchant_id,
        report_id=report.report_id,
        severity=_dossier_severity(report),
        probe_id=str(probe.probe_id),
        probe_status=probe.status.value,
        probe_duration_ms=float(probe.latency_ms),
        probe_failure_mode=probe.failure_mode.value if probe.failure_mode else "",
        probe_amount=probe.amount,
        probe_currency=probe.currency,
        reconciliation_status=report.status.value,
        discrepancies=[field.value for field in report.discrepancies],
        webhook_amount=report.webhook_amount,
        webhook_currency=report.webhook_currency,
        chain_seq=chain_seq,
        prev_hash=prev_hash,
    )
    result = pdf_builder.build(request)

    entry = await chain.append(
        merchant_id=discrepancy.merchant_id,
        dossier_hash=result.sha256_hash,
        discrepancy_id=discrepancy.id,
    )

    discrepancy.dossier_path = str(result.output_path)
    discrepancy.dossier_hash = result.sha256_hash

    log.info(
        "discrepancy_dossier_linked",
        discrepancy_id=str(discrepancy.id),
        merchant_id=str(discrepancy.merchant_id),
        chain_seq=entry.seq,
        dossier_hash=result.sha256_hash,
    )
    return discrepancy


def discrepancy_from_report(
    report: ReconciliationReport,
    description: str,
) -> ReconDiscrepancy:
    """Create an in-memory ReconDiscrepancy row from a reconciliation report."""
    return ReconDiscrepancy(
        id=uuid.uuid4(),
        merchant_id=report.merchant_id,
        severity=_severity_for_report(report).value,
        description=description,
        webhook_event_id=None,
    )
