"""Tests for scheduled probe/webhook reconciliation job logic."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.models.probe_result import ProbeResult as ProbeResultRow
from app.models.recon_discrepancy import ReconDiscrepancy
from app.models.webhook_event import WebhookEvent
from app.reconciliation.job import (
    PROBE_ONLY_DESCRIPTION,
    WEBHOOK_ONLY_DESCRIPTION,
    compute_pending_discrepancies,
    filter_new_discrepancies,
    run_reconciliation,
)

_MERCHANT = uuid.uuid4()
_NOW = datetime(2024, 6, 1, 14, 0, 0, tzinfo=UTC)


def _probe_row(
    intent_id: str = "pi_probe_001",
    probed_at: datetime = _NOW - timedelta(minutes=30),
    probe_id: uuid.UUID | None = None,
) -> ProbeResultRow:
    return ProbeResultRow(
        id=probe_id or uuid.uuid4(),
        merchant_id=_MERCHANT,
        status="succeeded",
        failure_mode=None,
        stripe_intent_id=intent_id,
        idempotency_key="key-1",
        latency_ms=10,
        raw_response_hash="abc",
        amount=100,
        currency="usd",
        probed_at=probed_at,
    )


def _webhook_row(
    intent_id: str = "pi_webhook_001",
    received_at: datetime = _NOW - timedelta(minutes=10),
    webhook_id: uuid.UUID | None = None,
) -> WebhookEvent:
    return WebhookEvent(
        id=webhook_id or uuid.uuid4(),
        merchant_id=_MERCHANT,
        stripe_event_id="evt_001",
        event_type="payment_intent.succeeded",
        payment_intent_id=intent_id,
        amount=100,
        currency="usd",
        livemode=False,
        api_version="2024-06-20",
        object_status="succeeded",
        stripe_created_at=received_at,
        received_at=received_at,
        metadata_json={},
    )


class TestComputePendingDiscrepancies:
    def test_probe_without_webhook_after_settlement_lag_is_high(self) -> None:
        pending = compute_pending_discrepancies(
            merchant_id=_MERCHANT,
            probes=[_probe_row(intent_id="pi_orphan")],
            webhooks=[],
            now=_NOW,
            settlement_lag_minutes=15,
        )
        assert len(pending) == 1
        assert pending[0].severity == "HIGH"
        assert pending[0].description == PROBE_ONLY_DESCRIPTION
        assert pending[0].stripe_intent_id == "pi_orphan"

    def test_probe_without_webhook_before_settlement_lag_is_ignored(self) -> None:
        recent_probe = _probe_row(
            intent_id="pi_recent",
            probed_at=_NOW - timedelta(minutes=5),
        )
        pending = compute_pending_discrepancies(
            merchant_id=_MERCHANT,
            probes=[recent_probe],
            webhooks=[],
            now=_NOW,
            settlement_lag_minutes=15,
        )
        assert pending == []

    def test_webhook_without_probe_is_medium(self) -> None:
        pending = compute_pending_discrepancies(
            merchant_id=_MERCHANT,
            probes=[],
            webhooks=[_webhook_row(intent_id="pi_webhook_only")],
            now=_NOW,
            settlement_lag_minutes=15,
        )
        assert len(pending) == 1
        assert pending[0].severity == "MEDIUM"
        assert pending[0].description == WEBHOOK_ONLY_DESCRIPTION

    def test_matched_probe_and_webhook_produce_no_discrepancy(self) -> None:
        intent = "pi_matched"
        pending = compute_pending_discrepancies(
            merchant_id=_MERCHANT,
            probes=[_probe_row(intent_id=intent)],
            webhooks=[_webhook_row(intent_id=intent)],
            now=_NOW,
            settlement_lag_minutes=15,
        )
        assert pending == []


class TestFilterNewDiscrepancies:
    def test_skips_existing_unresolved_identity(self) -> None:
        probe_id = uuid.uuid4()
        pending = compute_pending_discrepancies(
            merchant_id=_MERCHANT,
            probes=[_probe_row(intent_id="pi_dup", probe_id=probe_id)],
            webhooks=[],
            now=_NOW,
            settlement_lag_minutes=15,
        )
        existing = [
            ReconDiscrepancy(
                merchant_id=_MERCHANT,
                severity="HIGH",
                description=PROBE_ONLY_DESCRIPTION,
                stripe_intent_id="pi_dup",
                probe_result_id=probe_id,
                webhook_event_id=None,
                detected_at=_NOW,
            )
        ]
        assert filter_new_discrepancies(pending, existing) == []


@pytest.mark.asyncio
async def test_run_reconciliation_inserts_new_discrepancy(settings_override) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    merchant_id = _MERCHANT
    probe = _probe_row(intent_id="pi_job_test")

    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    def _execute_results(existing: list[ReconDiscrepancy]) -> list[MagicMock]:
        probe_result = MagicMock()
        probe_result.scalars.return_value.all.return_value = [probe]
        webhook_result = MagicMock()
        webhook_result.scalars.return_value.all.return_value = []
        discrepancy_result = MagicMock()
        discrepancy_result.scalars.return_value.all.return_value = existing
        return [probe_result, webhook_result, discrepancy_result]

    session.execute = AsyncMock(side_effect=_execute_results([]))

    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    count = await run_reconciliation(merchant_id, session, redis, settings, now=_NOW)
    assert count == 1
    session.add.assert_called_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_reconciliation_skips_when_unresolved_exists(settings_override) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    merchant_id = _MERCHANT
    probe = _probe_row(intent_id="pi_job_test")
    existing = ReconDiscrepancy(
        merchant_id=_MERCHANT,
        severity="HIGH",
        description=PROBE_ONLY_DESCRIPTION,
        stripe_intent_id="pi_job_test",
        probe_result_id=probe.id,
        webhook_event_id=None,
        detected_at=_NOW,
    )

    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    probe_result = MagicMock()
    probe_result.scalars.return_value.all.return_value = [probe]
    webhook_result = MagicMock()
    webhook_result.scalars.return_value.all.return_value = []
    discrepancy_result = MagicMock()
    discrepancy_result.scalars.return_value.all.return_value = [existing]
    session.execute = AsyncMock(side_effect=[probe_result, webhook_result, discrepancy_result])

    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    count = await run_reconciliation(merchant_id, session, redis, settings, now=_NOW)
    assert count == 0
    session.add.assert_not_called()
