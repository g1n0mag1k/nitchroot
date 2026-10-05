"""Tests for high-severity discrepancy dossier job wiring."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.artifacts.chain import SnapshotChain
from app.artifacts.pdf_builder import PdfDossierBuilder
from app.probes.models import ProbeResult, ProbeStatus
from app.reconciliation.discrepancy import DiscrepancySeverity, ReconDiscrepancy
from app.reconciliation.dossier_job import build_dossier_for_discrepancy
from app.reconciliation.engine import ReconciliationEngine
from app.reconciliation.models import ReconciliationStatus
from app.webhooks.models import NormalizedWebhookEvent, WebhookEventType

from tests.test_chain import FakeRedis

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)


def _make_probe(merchant_id: uuid.UUID) -> ProbeResult:
    return ProbeResult(
        probe_id=uuid.uuid4(),
        merchant_id=merchant_id,
        status=ProbeStatus.SUCCEEDED,
        latency_ms=10,
        probed_at=_NOW,
        stripe_intent_id="pi_test",
        amount=100,
        currency="usd",
    )


def _make_webhook() -> NormalizedWebhookEvent:
    return NormalizedWebhookEvent(
        stripe_event_id="evt_test",
        event_type=WebhookEventType.PAYMENT_INTENT_SUCCEEDED,
        livemode=False,
        api_version="2024-06-20",
        created_at=_NOW,
        object_id="pi_test",
        object_status="succeeded",
        amount=999,
        currency="usd",
    )


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


class TestBuildDossierForDiscrepancy:
    async def test_high_discrepancy_updates_row_and_chain(
        self, fake_redis: FakeRedis, tmp_path: Path
    ) -> None:
        merchant_id = uuid.uuid4()
        probe = _make_probe(merchant_id)
        webhook = _make_webhook()
        report = ReconciliationEngine().reconcile(probe, webhook)
        assert report.status == ReconciliationStatus.DISCREPANCY

        discrepancy = ReconDiscrepancy(
            id=uuid.uuid4(),
            merchant_id=merchant_id,
            severity=DiscrepancySeverity.HIGH.value,
            description="amount mismatch",
        )
        builder = PdfDossierBuilder(output_dir=tmp_path)
        updated = await build_dossier_for_discrepancy(
            discrepancy,
            report,
            probe,
            webhook,
            fake_redis,
            builder=builder,
        )
        assert updated.dossier_path is not None
        assert updated.dossier_hash is not None
        assert len(updated.dossier_hash) == 64

        chain = SnapshotChain(fake_redis)
        entries = await chain.export(merchant_id)
        assert len(entries) == 1
        assert entries[0].dossier_hash == updated.dossier_hash
        assert entries[0].prev_hash == "GENESIS"

    async def test_non_high_skips_dossier(self, fake_redis: FakeRedis, tmp_path: Path) -> None:
        merchant_id = uuid.uuid4()
        probe = _make_probe(merchant_id)
        report = ReconciliationEngine().reconcile(probe, None)
        discrepancy = ReconDiscrepancy(
            id=uuid.uuid4(),
            merchant_id=merchant_id,
            severity=DiscrepancySeverity.LOW.value,
            description="probe only",
        )
        builder = PdfDossierBuilder(output_dir=tmp_path)
        updated = await build_dossier_for_discrepancy(
            discrepancy,
            report,
            probe,
            None,
            fake_redis,
            builder=builder,
        )
        assert updated.dossier_path is None
        assert updated.dossier_hash is None
        chain = SnapshotChain(fake_redis)
        assert await chain.export(merchant_id) == []
