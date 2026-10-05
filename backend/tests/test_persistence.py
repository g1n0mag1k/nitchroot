"""Integration tests for the SQLAlchemy persistence layer."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from app.persistence.encryption import MerchantKeyEncryption
from app.persistence.mappers import (
    decrypt_merchant_stripe_key,
    merchant_to_orm,
    orm_to_probe_result,
    orm_to_reconciliation_report,
    probe_result_to_orm,
    reconciliation_report_to_orm,
    webhook_event_to_orm,
)
from app.persistence.models import (
    Merchant,
    ProbeResultRow,
    ReconciliationReportRow,
    WebhookEventRow,
)
from app.probes.models import ProbeResult, ProbeStatus
from app.reconciliation.models import DiscrepancyField, ReconciliationReport, ReconciliationStatus
from app.webhooks.models import NormalizedWebhookEvent, WebhookEventType
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

pytestmark = pytest.mark.usefixtures("db_schema")


async def test_merchant_creation(db_session: AsyncSession) -> None:
    merchant = merchant_to_orm(
        name="Acme Payments",
        stripe_restricted_key="rk_test_acme_restricted_key",
        probe_interval_seconds=600,
    )
    db_session.add(merchant)
    await db_session.flush()

    loaded = await db_session.get(Merchant, merchant.id)
    assert loaded is not None
    assert loaded.name == "Acme Payments"
    assert loaded.probe_interval_seconds == 600
    assert loaded.is_enabled is True


async def test_encrypted_stripe_key_round_trip(db_session: AsyncSession) -> None:
    plaintext = "rk_test_roundtrip_key_value"
    merchant = merchant_to_orm(name="Encrypt Co", stripe_restricted_key=plaintext)
    db_session.add(merchant)
    await db_session.flush()

    assert plaintext.encode() not in merchant.stripe_restricted_key_enc
    assert decrypt_merchant_stripe_key(merchant) == plaintext


async def test_probe_result_persistence(db_session: AsyncSession) -> None:
    merchant = merchant_to_orm(
        name="Probe Merchant",
        stripe_restricted_key="rk_test_probe",
    )
    db_session.add(merchant)
    await db_session.flush()

    probe_id = uuid.uuid4()
    result = ProbeResult(
        probe_id=probe_id,
        merchant_id=merchant.id,
        status=ProbeStatus.SUCCEEDED,
        latency_ms=42,
        probed_at=datetime.now(UTC),
        stripe_intent_id="pi_test123",
        idempotency_key="idem-1",
        raw_response_hash="abc123",
    )
    row = probe_result_to_orm(result)
    db_session.add(row)
    await db_session.flush()

    loaded = await db_session.get(ProbeResultRow, probe_id)
    assert loaded is not None
    round_trip = orm_to_probe_result(loaded)
    assert round_trip.probe_id == probe_id
    assert round_trip.stripe_intent_id == "pi_test123"
    assert round_trip.succeeded is True


async def test_webhook_event_persistence(db_session: AsyncSession) -> None:
    merchant = merchant_to_orm(
        name="Webhook Merchant",
        stripe_restricted_key="rk_test_webhook",
    )
    db_session.add(merchant)
    await db_session.flush()

    event = NormalizedWebhookEvent(
        stripe_event_id="evt_123",
        event_type=WebhookEventType.PAYMENT_INTENT_SUCCEEDED,
        livemode=False,
        api_version="2024-06-20",
        object_id="pi_abc",
        object_status="succeeded",
        amount=100,
        currency="usd",
        metadata={"probe_id": "p1"},
    )
    row = webhook_event_to_orm(event, merchant_id=merchant.id)
    db_session.add(row)
    await db_session.flush()

    loaded = await db_session.get(WebhookEventRow, event.internal_id)
    assert loaded is not None
    assert loaded.stripe_event_id == "evt_123"
    assert loaded.sanitized_metadata == {"probe_id": "p1"}


async def test_webhook_stripe_event_id_unique(db_session: AsyncSession) -> None:
    merchant = merchant_to_orm(
        name="Dedup Merchant",
        stripe_restricted_key="rk_test_dedup",
    )
    db_session.add(merchant)
    await db_session.flush()

    first = webhook_event_to_orm(
        NormalizedWebhookEvent(stripe_event_id="evt_dup"),
        merchant_id=merchant.id,
    )
    second = webhook_event_to_orm(
        NormalizedWebhookEvent(stripe_event_id="evt_dup"),
        merchant_id=merchant.id,
    )
    db_session.add(first)
    await db_session.flush()
    db_session.add(second)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_reconciliation_report_persistence(db_session: AsyncSession) -> None:
    merchant = merchant_to_orm(
        name="Recon Merchant",
        stripe_restricted_key="rk_test_recon",
    )
    db_session.add(merchant)
    await db_session.flush()

    probe_id = uuid.uuid4()
    probe_row = probe_result_to_orm(
        ProbeResult(
            probe_id=probe_id,
            merchant_id=merchant.id,
            status=ProbeStatus.SUCCEEDED,
            latency_ms=10,
            probed_at=datetime.now(UTC),
            stripe_intent_id="pi_recon",
        ),
    )
    webhook = webhook_event_to_orm(
        NormalizedWebhookEvent(
            stripe_event_id="evt_recon",
            event_type=WebhookEventType.PAYMENT_INTENT_SUCCEEDED,
            object_id="pi_recon",
            amount=100,
            currency="usd",
        ),
        merchant_id=merchant.id,
    )
    db_session.add_all([probe_row, webhook])
    await db_session.flush()

    report = ReconciliationReport(
        merchant_id=merchant.id,
        probe_id=probe_id,
        stripe_event_id="evt_recon",
        stripe_intent_id="pi_recon",
        status=ReconciliationStatus.DISCREPANCY,
        discrepancies=[DiscrepancyField.AMOUNT],
        probe_amount=100,
        webhook_amount=200,
        probe_currency="usd",
        webhook_currency="usd",
    )
    report_row = reconciliation_report_to_orm(
        report,
        webhook_event_id=webhook.id,
        dossier_path="/tmp/dossier.pdf",
        dossier_sha256="deadbeef",
    )
    db_session.add(report_row)
    await db_session.flush()

    loaded = await db_session.get(ReconciliationReportRow, report.report_id)
    assert loaded is not None
    round_trip = orm_to_reconciliation_report(loaded)
    assert round_trip.status == ReconciliationStatus.DISCREPANCY
    assert round_trip.discrepancies == [DiscrepancyField.AMOUNT]
    assert loaded.dossier_sha256 == "deadbeef"


async def test_webhook_events_have_no_raw_payload_column(
    test_engine: AsyncEngine,
) -> None:
    async with test_engine.connect() as conn:
        columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"] for column in inspect(sync_conn).get_columns("webhook_events")
            },
        )
    forbidden = {
        "raw_payload",
        "payload",
        "raw_body",
        "stripe_payload",
        "event_payload",
    }
    assert forbidden.isdisjoint(columns)


async def test_merchant_stripe_key_not_stored_plaintext(db_session: AsyncSession) -> None:
    plaintext = "rk_test_must_not_appear_plain"
    merchant = merchant_to_orm(name="Plaintext Guard", stripe_restricted_key=plaintext)
    db_session.add(merchant)
    await db_session.flush()

    result = await db_session.execute(
        select(Merchant.stripe_restricted_key_enc).where(Merchant.id == merchant.id),
    )
    blob = result.scalar_one()
    assert isinstance(blob, bytes)
    assert plaintext.encode("utf-8") not in blob
    assert MerchantKeyEncryption().decrypt(blob) == plaintext
