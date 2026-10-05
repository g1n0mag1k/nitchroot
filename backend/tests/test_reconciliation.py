"""Tests for the ledger reconciliation engine.

All objects are constructed in-memory. No DB, no Stripe, no Redis calls.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.probes.models import ProbeResult, ProbeStatus
from app.reconciliation.engine import ReconciliationEngine
from app.reconciliation.models import (
    DiscrepancyField,
    ReconciliationStatus,
)
from app.webhooks.models import NormalizedWebhookEvent, WebhookEventType

# ── helpers ──────────────────────────────────────────────────────────────────

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
_MERCHANT = uuid.uuid4()
_PROBE_UUID = uuid.uuid4()
_INTENT_ID = "pi_test_001"
_EVENT_ID = "evt_test_001"


def _make_probe(
    amount: int = 100,
    currency: str = "usd",
    probed_at: datetime = _NOW,
    status: ProbeStatus = ProbeStatus.SUCCEEDED,
    merchant_id: uuid.UUID = _MERCHANT,
    probe_id: uuid.UUID = _PROBE_UUID,
) -> ProbeResult:
    return ProbeResult(
        probe_id=probe_id,
        merchant_id=merchant_id,
        status=status,
        probed_at=probed_at,
        latency_ms=42,
        stripe_intent_id=_INTENT_ID,
        amount=amount,
        currency=currency,
        idempotency_key=f"snitchroot-probe-{probe_id}",
    )


def _make_webhook(
    amount: int = 100,
    currency: str = "usd",
    created_at: datetime = _NOW,
    stripe_event_id: str = _EVENT_ID,
    object_id: str = _INTENT_ID,
) -> NormalizedWebhookEvent:
    return NormalizedWebhookEvent(
        stripe_event_id=stripe_event_id,
        event_type=WebhookEventType.PAYMENT_INTENT_SUCCEEDED,
        livemode=False,
        api_version="2024-06-20",
        created_at=created_at,
        object_id=object_id,
        object_status="succeeded",
        amount=amount,
        currency=currency,
    )


# ── engine tests ──────────────────────────────────────────────────────────────


class TestReconciliationEngine:
    def test_matched_when_probe_and_webhook_agree(self) -> None:
        engine = ReconciliationEngine()
        report = engine.reconcile(_make_probe(), _make_webhook())
        assert report.status == ReconciliationStatus.MATCHED
        assert report.discrepancies == []
        assert report.is_clean is True

    def test_probe_only_when_webhook_is_none(self) -> None:
        engine = ReconciliationEngine()
        report = engine.reconcile(_make_probe(), None)
        assert report.status == ReconciliationStatus.PROBE_ONLY
        assert report.stripe_event_id is None
        assert report.probe_amount == 100

    def test_webhook_only_when_probe_is_none(self) -> None:
        engine = ReconciliationEngine()
        report = engine.reconcile(None, _make_webhook())
        assert report.status == ReconciliationStatus.WEBHOOK_ONLY
        assert report.probe_id is None
        assert report.webhook_amount == 100

    def test_raises_when_both_none(self) -> None:
        engine = ReconciliationEngine()
        with pytest.raises(ValueError, match="At least one"):
            engine.reconcile(None, None)

    def test_discrepancy_on_amount_mismatch(self) -> None:
        engine = ReconciliationEngine()
        report = engine.reconcile(
            _make_probe(amount=100),
            _make_webhook(amount=999),
        )
        assert report.status == ReconciliationStatus.DISCREPANCY
        assert DiscrepancyField.AMOUNT in report.discrepancies
        assert report.is_clean is False

    def test_discrepancy_on_currency_mismatch(self) -> None:
        engine = ReconciliationEngine()
        report = engine.reconcile(
            _make_probe(currency="usd"),
            _make_webhook(currency="eur"),
        )
        assert report.status == ReconciliationStatus.DISCREPANCY
        assert DiscrepancyField.CURRENCY in report.discrepancies

    def test_multiple_discrepancies_captured(self) -> None:
        engine = ReconciliationEngine()
        report = engine.reconcile(
            _make_probe(amount=100, currency="usd"),
            _make_webhook(amount=200, currency="eur"),
        )
        assert report.status == ReconciliationStatus.DISCREPANCY
        assert len(report.discrepancies) == 2
        assert DiscrepancyField.AMOUNT in report.discrepancies
        assert DiscrepancyField.CURRENCY in report.discrepancies

    def test_outside_window_treated_as_probe_only(self) -> None:
        engine = ReconciliationEngine(window_seconds=300)
        late_webhook = _make_webhook(
            created_at=_NOW + timedelta(seconds=301)  # 1 second past window
        )
        report = engine.reconcile(_make_probe(), late_webhook)
        assert report.status == ReconciliationStatus.PROBE_ONLY

    def test_exactly_at_window_boundary_is_matched(self) -> None:
        engine = ReconciliationEngine(window_seconds=300)
        edge_webhook = _make_webhook(
            created_at=_NOW + timedelta(seconds=300)  # exactly at limit
        )
        report = engine.reconcile(_make_probe(), edge_webhook)
        assert report.status == ReconciliationStatus.MATCHED

    def test_custom_window_respected(self) -> None:
        engine = ReconciliationEngine(window_seconds=60)
        late_webhook = _make_webhook(created_at=_NOW + timedelta(seconds=61))
        report = engine.reconcile(_make_probe(), late_webhook)
        assert report.status == ReconciliationStatus.PROBE_ONLY

    def test_report_captures_both_amounts_on_discrepancy(self) -> None:
        engine = ReconciliationEngine()
        report = engine.reconcile(
            _make_probe(amount=100),
            _make_webhook(amount=500),
        )
        assert report.probe_amount == 100
        assert report.webhook_amount == 500

    def test_report_is_frozen(self) -> None:
        """ReconciliationReport must be immutable."""
        engine = ReconciliationEngine()
        report = engine.reconcile(_make_probe(), _make_webhook())
        with pytest.raises((AttributeError, TypeError)):
            report.status = ReconciliationStatus.DISCREPANCY  # type: ignore[misc]

    def test_is_clean_false_on_discrepancy(self) -> None:
        engine = ReconciliationEngine()
        report = engine.reconcile(
            _make_probe(amount=100),
            _make_webhook(amount=200),
        )
        assert report.is_clean is False

    def test_report_ids_are_unique_per_call(self) -> None:
        engine = ReconciliationEngine()
        r1 = engine.reconcile(_make_probe(), _make_webhook())
        r2 = engine.reconcile(_make_probe(), _make_webhook())
        assert r1.report_id != r2.report_id
