"""Incident, dossier integrity, alerting, and worker integration tests."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx
from app.alerting.channels import PermanentAlertError, TransientAlertError, WebhookAlertChannel
from app.alerting.dispatcher import AlertDispatcher
from app.alerting.payload import build_alert_payload
from app.artifacts.digest import sha256_file
from app.artifacts.models import DossierRequest, DossierSeverity
from app.artifacts.pdf_builder import PdfDossierBuilder
from app.artifacts.verification import verify_dossier
from app.incidents.policy import requires_incident
from app.incidents.service import IncidentService
from app.persistence.enums import AlertDeliveryStatus
from app.persistence.models import AlertDeliveryRow, ReconciliationJob, ReconDiscrepancy
from app.reconciliation.models import ReconciliationReport, ReconciliationStatus
from sqlalchemy import func, select
from workers.reconciliation_worker import ReconciliationWorker

from tests.test_worker import _seed_webhook_job


class TestIncidentPolicy:
    def test_matched_does_not_require_incident(self) -> None:
        report = ReconciliationReport(status=ReconciliationStatus.MATCHED)
        assert requires_incident(report) is False

    def test_discrepancy_requires_incident(self) -> None:
        report = ReconciliationReport(status=ReconciliationStatus.DISCREPANCY)
        assert requires_incident(report) is True

    def test_probe_only_requires_incident(self) -> None:
        report = ReconciliationReport(status=ReconciliationStatus.PROBE_ONLY)
        assert requires_incident(report) is True


@pytest.mark.asyncio
async def test_matched_does_not_create_incident(integration_session_factory) -> None:
    async with integration_session_factory() as session:
        async with session.begin():
            job, _ = await _seed_webhook_job(session, with_probe=True)
            job_id = job.id

    service = IncidentService()
    report = ReconciliationReport(
        merchant_id=uuid.uuid4(),
        status=ReconciliationStatus.MATCHED,
    )
    async with integration_session_factory() as session:
        async with session.begin():
            job = await session.get(ReconciliationJob, job_id)
            assert job is not None
            incident = await service.handle_reconciliation_outcome(session, job, report)
            assert incident is None


@pytest.mark.asyncio
async def test_discrepancy_creates_incident(integration_session_factory, tmp_path: Path) -> None:
    async with integration_session_factory() as session:
        async with session.begin():
            job, merchant = await _seed_webhook_job(session, with_probe=True)
            job_id = job.id
            merchant_id = merchant.id

    service = IncidentService(
        dossier_builder=PdfDossierBuilder(output_dir=tmp_path),
        alert_dispatcher=AlertDispatcher(channels=[]),
    )
    report = ReconciliationReport(
        merchant_id=merchant_id,
        status=ReconciliationStatus.DISCREPANCY,
        stripe_intent_id="pi_test_shared",
    )
    async with integration_session_factory() as session:
        async with session.begin():
            job = await session.get(ReconciliationJob, job_id)
            assert job is not None
            incident = await service.handle_reconciliation_outcome(session, job, report)
            assert incident is not None
            assert incident.merchant_id == merchant_id
            assert incident.reconciliation_job_id == job_id


@pytest.mark.asyncio
async def test_duplicate_incident_not_created(integration_session_factory, tmp_path: Path) -> None:
    async with integration_session_factory() as session:
        async with session.begin():
            job, merchant = await _seed_webhook_job(session, with_probe=False)
            job_id = job.id

    service = IncidentService(
        dossier_builder=PdfDossierBuilder(output_dir=tmp_path),
        alert_dispatcher=AlertDispatcher(channels=[]),
    )
    report = ReconciliationReport(
        merchant_id=merchant.id,
        status=ReconciliationStatus.WEBHOOK_ONLY,
    )
    async with integration_session_factory() as session:
        async with session.begin():
            job = await session.get(ReconciliationJob, job_id)
            assert job is not None
            await service.handle_reconciliation_outcome(session, job, report)
            await service.handle_reconciliation_outcome(session, job, report)

    async with integration_session_factory() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(ReconDiscrepancy)
                .where(ReconDiscrepancy.reconciliation_job_id == job_id)
            )
        ).scalar_one()
        assert count == 1


@pytest.mark.asyncio
async def test_discrepancy_generates_dossier_and_manifest(
    integration_session_factory,
    tmp_path: Path,
) -> None:
    async with integration_session_factory() as session:
        async with session.begin():
            job, merchant = await _seed_webhook_job(session, with_probe=True)
            job_id = job.id

    service = IncidentService(
        dossier_builder=PdfDossierBuilder(output_dir=tmp_path),
        alert_dispatcher=AlertDispatcher(channels=[]),
    )
    report = ReconciliationReport(
        merchant_id=merchant.id,
        status=ReconciliationStatus.DISCREPANCY,
        stripe_intent_id="pi_test_shared",
    )
    async with integration_session_factory() as session:
        async with session.begin():
            job = await session.get(ReconciliationJob, job_id)
            incident = await service.handle_reconciliation_outcome(session, job, report)
            assert incident is not None
            assert incident.dossier_path is not None
            assert incident.manifest_path is not None
            pdf_path = Path(incident.dossier_path)
            manifest_path = Path(incident.manifest_path)
            assert pdf_path.exists()
            assert manifest_path.exists()
            assert incident.dossier_sha256 == sha256_file(pdf_path)
            manifest = json.loads(manifest_path.read_text())
            assert manifest["dossier_sha256"] == incident.dossier_sha256


@pytest.mark.asyncio
async def test_dossier_failure_does_not_erase_incident(
    integration_session_factory,
    tmp_path: Path,
) -> None:
    async with integration_session_factory() as session:
        async with session.begin():
            job, merchant = await _seed_webhook_job(session)
            job_id = job.id

    broken_builder = MagicMock()
    broken_builder.build.side_effect = RuntimeError("disk full")
    service = IncidentService(
        dossier_builder=broken_builder,
        alert_dispatcher=AlertDispatcher(channels=[]),
    )
    report = ReconciliationReport(
        merchant_id=merchant.id,
        status=ReconciliationStatus.WEBHOOK_ONLY,
    )
    async with integration_session_factory() as session:
        async with session.begin():
            job = await session.get(ReconciliationJob, job_id)
            incident = await service.handle_reconciliation_outcome(session, job, report)
            assert incident is not None
            assert incident.dossier_error is not None

    async with integration_session_factory() as session:
        row = (
            await session.execute(
                select(ReconDiscrepancy).where(ReconDiscrepancy.reconciliation_job_id == job_id)
            )
        ).scalar_one()
        assert row.id == incident.id


@pytest.mark.asyncio
async def test_alert_dispatch_persisted(integration_session_factory, tmp_path: Path) -> None:
    webhook_url = "https://alerts.example/hook"
    async with integration_session_factory() as session:
        async with session.begin():
            job, merchant = await _seed_webhook_job(session)
            job_id = job.id

    with respx.mock:
        route = respx.post(webhook_url).mock(return_value=httpx.Response(200))
        channel = WebhookAlertChannel(webhook_url, timeout_seconds=5.0)
        service = IncidentService(
            dossier_builder=PdfDossierBuilder(output_dir=tmp_path),
            alert_dispatcher=AlertDispatcher(channels=[channel]),
        )
        report = ReconciliationReport(
            merchant_id=merchant.id,
            status=ReconciliationStatus.DISCREPANCY,
        )
        async with integration_session_factory() as session:
            async with session.begin():
                job = await session.get(ReconciliationJob, job_id)
                incident = await service.handle_reconciliation_outcome(session, job, report)
                assert incident is not None

        assert route.called
        async with integration_session_factory() as session:
            delivery = (
                await session.execute(
                    select(AlertDeliveryRow).where(AlertDeliveryRow.incident_id == incident.id)
                )
            ).scalar_one()
            assert delivery.status == AlertDeliveryStatus.SENT


@pytest.mark.asyncio
async def test_alert_payload_excludes_secrets(integration_session_factory) -> None:
    incident = ReconDiscrepancy(
        id=uuid.uuid4(),
        merchant_id=uuid.uuid4(),
        reconciliation_job_id=uuid.uuid4(),
        webhook_event_id=None,
        probe_id=None,
        payment_intent_id="pi_safe123",
        discrepancy_type=ReconciliationStatus.DISCREPANCY.value,
        severity="high",
        status="open",
        detected_at=datetime.now(tz=UTC),
        dossier_path="/tmp/incident_x.pdf",
        dossier_sha256="a" * 64,
        manifest_path="/tmp/incident_x.manifest.json",
    )
    payload = build_alert_payload(incident)
    body = payload.to_dict()
    serialized = json.dumps(body)
    assert "whsec_" not in serialized
    assert "sk_" not in serialized
    assert "raw" not in serialized.lower()
    assert "authorization" not in serialized.lower()
    assert body["payment_intent_id"] == "pi_safe123"


@pytest.mark.asyncio
async def test_duplicate_alert_delivery_not_created(
    integration_session_factory,
    tmp_path: Path,
) -> None:
    webhook_url = "https://alerts.example/hook"
    async with integration_session_factory() as session:
        async with session.begin():
            job, merchant = await _seed_webhook_job(session)
            job_id = job.id

    with respx.mock:
        route = respx.post(webhook_url).mock(return_value=httpx.Response(200))
        channel = WebhookAlertChannel(webhook_url, timeout_seconds=5.0)
        dispatcher = AlertDispatcher(channels=[channel])
        service = IncidentService(
            dossier_builder=PdfDossierBuilder(output_dir=tmp_path),
            alert_dispatcher=dispatcher,
        )
        report = ReconciliationReport(
            merchant_id=merchant.id,
            status=ReconciliationStatus.PROBE_ONLY,
        )
        async with integration_session_factory() as session:
            async with session.begin():
                job = await session.get(ReconciliationJob, job_id)
                incident = await service.handle_reconciliation_outcome(session, job, report)

        async with integration_session_factory() as session:
            async with session.begin():
                await dispatcher.dispatch_incident(session, incident)

        assert route.call_count == 1
        async with integration_session_factory() as session:
            count = (
                await session.execute(
                    select(func.count())
                    .select_from(AlertDeliveryRow)
                    .where(AlertDeliveryRow.incident_id == incident.id)
                )
            ).scalar_one()
            assert count == 1


@pytest.mark.asyncio
async def test_transient_alert_failure_retryable(integration_session_factory) -> None:
    incident_id = uuid.uuid4()
    async with integration_session_factory() as session:
        async with session.begin():
            job, merchant = await _seed_webhook_job(session)
            session.add(
                ReconDiscrepancy(
                    id=incident_id,
                    merchant_id=merchant.id,
                    reconciliation_job_id=job.id,
                    webhook_event_id=job.webhook_event_id,
                    discrepancy_type=ReconciliationStatus.DISCREPANCY.value,
                    severity="high",
                    status="open",
                    detected_at=datetime.now(tz=UTC),
                )
            )

    channel = AsyncMock()
    channel.name = "webhook"
    channel.send.side_effect = TransientAlertError("upstream 503")
    dispatcher = AlertDispatcher(channels=[channel])

    async with integration_session_factory() as session:
        async with session.begin():
            incident = await session.get(ReconDiscrepancy, incident_id)
            await dispatcher.dispatch_incident(session, incident)

    async with integration_session_factory() as session:
        delivery = (
            await session.execute(
                select(AlertDeliveryRow).where(AlertDeliveryRow.incident_id == incident_id)
            )
        ).scalar_one()
        assert delivery.status == AlertDeliveryStatus.RETRYABLE


@pytest.mark.asyncio
async def test_permanent_alert_failure_not_retried(integration_session_factory) -> None:
    incident_id = uuid.uuid4()
    async with integration_session_factory() as session:
        async with session.begin():
            job, merchant = await _seed_webhook_job(session)
            session.add(
                ReconDiscrepancy(
                    id=incident_id,
                    merchant_id=merchant.id,
                    reconciliation_job_id=job.id,
                    webhook_event_id=job.webhook_event_id,
                    discrepancy_type=ReconciliationStatus.DISCREPANCY.value,
                    severity="high",
                    status="open",
                    detected_at=datetime.now(tz=UTC),
                )
            )

    channel = AsyncMock()
    channel.name = "webhook"
    channel.send.side_effect = PermanentAlertError("webhook returned 400")
    dispatcher = AlertDispatcher(channels=[channel])

    async with integration_session_factory() as session:
        async with session.begin():
            incident = await session.get(ReconDiscrepancy, incident_id)
            await dispatcher.dispatch_incident(session, incident)
            await dispatcher.dispatch_incident(session, incident)

    async with integration_session_factory() as session:
        delivery = (
            await session.execute(
                select(AlertDeliveryRow).where(AlertDeliveryRow.incident_id == incident_id)
            )
        ).scalar_one()
        assert delivery.status == AlertDeliveryStatus.FAILED
        assert channel.send.await_count == 1


@pytest.mark.asyncio
async def test_worker_invokes_incident_service(
    integration_session_factory,
    integration_redis,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARTIFACT_OUTPUT_DIR", str(tmp_path))
    get_settings = __import__("app.core.config", fromlist=["get_settings"]).get_settings
    get_settings.cache_clear()

    async with integration_session_factory() as session:
        async with session.begin():
            job, _ = await _seed_webhook_job(session, with_probe=False)
            job_id = job.id

    mock_service = AsyncMock()
    worker = ReconciliationWorker(
        integration_session_factory,
        integration_redis,
        incident_service=mock_service,
    )
    await worker._execute_job(job_id)
    mock_service.handle_reconciliation_outcome.assert_awaited()


@pytest.mark.asyncio
async def test_worker_retry_no_duplicate_incident(
    integration_session_factory,
    integration_redis,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ARTIFACT_OUTPUT_DIR", str(tmp_path))
    from app.core.config import get_settings

    get_settings.cache_clear()

    async with integration_session_factory() as session:
        async with session.begin():
            job, _ = await _seed_webhook_job(session, with_probe=False)
            job_id = job.id

    worker = ReconciliationWorker(integration_session_factory, integration_redis)
    await worker._execute_job(job_id)
    await worker._execute_job(job_id)

    async with integration_session_factory() as session:
        count = (
            await session.execute(
                select(func.count())
                .select_from(ReconDiscrepancy)
                .where(ReconDiscrepancy.reconciliation_job_id == job_id)
            )
        ).scalar_one()
        assert count == 1


def test_dossier_contains_no_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_super_secret_value")
    monkeypatch.setenv("STRIPE_RESTRICTED_KEY", "rk_test_super_secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    builder = PdfDossierBuilder(output_dir=tmp_path)
    req = DossierRequest(
        merchant_id=uuid.uuid4(),
        severity=DossierSeverity.CRITICAL,
        probe_id=str(uuid.uuid4()),
        probe_status="succeeded",
        reconciliation_status="discrepancy",
        discrepancies=["amount"],
    )
    result = builder.build(req)
    content = result.output_path.read_bytes()
    assert b"whsec_" not in content
    assert b"rk_test" not in content


def test_malformed_manifest_fails_verification(tmp_path: Path) -> None:
    pdf = tmp_path / "incident_test.pdf"
    manifest = tmp_path / "incident_test.manifest.json"
    pdf.write_bytes(b"%PDF-1.4")
    manifest.write_text("{not json", encoding="utf-8")
    result = verify_dossier(pdf, manifest)
    assert result.ok is False


def test_missing_pdf_fails_verification(tmp_path: Path) -> None:
    manifest = tmp_path / "incident_test.manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    result = verify_dossier(tmp_path / "missing.pdf", manifest)
    assert result.ok is False
    assert result.reason == "pdf_missing"


def test_manifest_tamper_fails_verification(tmp_path: Path) -> None:
    builder = PdfDossierBuilder(output_dir=tmp_path)
    result = builder.build(
        DossierRequest(
            merchant_id=uuid.uuid4(),
            severity=DossierSeverity.WARNING,
            reconciliation_status="probe_only",
        )
    )
    data = json.loads(result.manifest_path.read_text())
    data["dossier_sha256"] = "0" * 64
    result.manifest_path.write_text(json.dumps(data), encoding="utf-8")
    verification = verify_dossier(result.output_path, result.manifest_path)
    assert verification.ok is False
