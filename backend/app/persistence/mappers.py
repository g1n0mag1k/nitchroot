"""Map between domain dataclasses and SQLAlchemy ORM rows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.persistence.encryption import MerchantKeyEncryption
from app.persistence.models import (
    Merchant,
    MerchantOperationalMode,
    ProbeResultRow,
    ReconciliationReportRow,
    WebhookEventRow,
)
from app.probes.models import ProbeFailureMode, ProbeResult, ProbeStatus
from app.reconciliation.models import DiscrepancyField, ReconciliationReport, ReconciliationStatus
from app.webhooks.models import NormalizedWebhookEvent


def merchant_to_orm(
    *,
    name: str,
    stripe_restricted_key: str,
    probe_interval_seconds: int = 900,
    is_enabled: bool = True,
    operational_mode: MerchantOperationalMode = MerchantOperationalMode.SANDBOX,
    merchant_id: uuid.UUID | None = None,
    encrypter: MerchantKeyEncryption | None = None,
) -> Merchant:
    cipher = encrypter or MerchantKeyEncryption()
    return Merchant(
        id=merchant_id or uuid.uuid4(),
        name=name,
        stripe_restricted_key_enc=cipher.encrypt(stripe_restricted_key),
        probe_interval_seconds=probe_interval_seconds,
        is_enabled=is_enabled,
        operational_mode=operational_mode.value,
    )


def decrypt_merchant_stripe_key(
    merchant: Merchant,
    encrypter: MerchantKeyEncryption | None = None,
) -> str:
    cipher = encrypter or MerchantKeyEncryption()
    return cipher.decrypt(merchant.stripe_restricted_key_enc)


def probe_result_to_orm(result: ProbeResult) -> ProbeResultRow:
    return ProbeResultRow(
        id=result.probe_id,
        merchant_id=result.merchant_id,
        status=result.status.value,
        failure_mode=result.failure_mode.value if result.failure_mode else None,
        stripe_intent_id=result.stripe_intent_id,
        idempotency_key=result.idempotency_key,
        latency_ms=result.latency_ms,
        raw_response_hash=result.raw_response_hash,
        probed_at=_ensure_utc(result.probed_at),
        error_message=result.error_message,
        amount=result.amount,
        currency=result.currency,
    )


def orm_to_probe_result(row: ProbeResultRow) -> ProbeResult:
    return ProbeResult(
        probe_id=row.id,
        merchant_id=row.merchant_id,
        status=ProbeStatus(row.status),
        latency_ms=row.latency_ms,
        probed_at=row.probed_at,
        stripe_intent_id=row.stripe_intent_id,
        amount=row.amount,
        currency=row.currency,
        idempotency_key=row.idempotency_key,
        raw_response_hash=row.raw_response_hash,
        failure_mode=ProbeFailureMode(row.failure_mode) if row.failure_mode else None,
        error_message=row.error_message,
    )


def webhook_event_to_orm(
    event: NormalizedWebhookEvent,
    merchant_id: uuid.UUID,
    received_at: datetime | None = None,
) -> WebhookEventRow:
    return WebhookEventRow(
        id=event.internal_id,
        merchant_id=merchant_id,
        stripe_event_id=event.stripe_event_id,
        event_type=event.event_type.value,
        livemode=event.livemode,
        api_version=event.api_version,
        object_id=event.object_id,
        object_status=event.object_status,
        amount=event.amount,
        currency=event.currency,
        event_created_at=_ensure_utc(event.created_at),
        received_at=_ensure_utc(received_at or datetime.now(UTC)),
        sanitized_metadata=dict(event.metadata),
    )


def reconciliation_report_to_orm(
    report: ReconciliationReport,
    *,
    webhook_event_id: uuid.UUID | None = None,
    dossier_path: str | None = None,
    dossier_sha256: str | None = None,
) -> ReconciliationReportRow:
    return ReconciliationReportRow(
        id=report.report_id,
        merchant_id=report.merchant_id,
        probe_id=report.probe_id,
        webhook_event_id=webhook_event_id,
        stripe_event_id=report.stripe_event_id,
        stripe_intent_id=report.stripe_intent_id,
        status=report.status.value,
        discrepancies=[d.value for d in report.discrepancies],
        probe_amount=report.probe_amount,
        webhook_amount=report.webhook_amount,
        probe_currency=report.probe_currency,
        webhook_currency=report.webhook_currency,
        window_seconds=report.window_seconds,
        reconciled_at=_ensure_utc(report.reconciled_at),
        dossier_path=dossier_path,
        dossier_sha256=dossier_sha256,
    )


def orm_to_reconciliation_report(row: ReconciliationReportRow) -> ReconciliationReport:
    return ReconciliationReport(
        report_id=row.id,
        merchant_id=row.merchant_id,
        probe_id=row.probe_id,
        stripe_event_id=row.stripe_event_id,
        stripe_intent_id=row.stripe_intent_id,
        status=ReconciliationStatus(row.status),
        discrepancies=[DiscrepancyField(value) for value in row.discrepancies],
        probe_amount=row.probe_amount,
        webhook_amount=row.webhook_amount,
        probe_currency=row.probe_currency,
        webhook_currency=row.webhook_currency,
        reconciled_at=row.reconciled_at,
        window_seconds=row.window_seconds,
    )


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
