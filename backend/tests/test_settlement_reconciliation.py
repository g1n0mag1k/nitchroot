"""BalanceTransaction settlement checks after probe/webhook reconciliation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import stripe
from app.probes.balance_transaction_client import (
    BalanceTransactionCheckResult,
    BalanceTransactionClient,
)
from app.probes.models import ProbeResult, ProbeStatus
from app.reconciliation.engine import ReconciliationEngine
from app.reconciliation.models import DiscrepancySeverity, ReconciliationStatus
from app.reconciliation.settlement import (
    reconcile_with_settlement_check,
    verify_settlement_after_match,
)
from app.webhooks.models import NormalizedWebhookEvent, WebhookEventType
from workers.runner import (
    InMemoryReconciliationPairSource,
    ReconciliationWorkItem,
    run_reconciliation_job,
)

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
_MERCHANT = uuid.uuid4()
_PROBE_ID = uuid.uuid4()
_INTENT = "pi_test_settlement"
_EVENT = "evt_test_settlement"
_KEY = "rk_test_settlement"


def _probe() -> ProbeResult:
    return ProbeResult(
        probe_id=_PROBE_ID,
        merchant_id=_MERCHANT,
        status=ProbeStatus.SUCCEEDED,
        probed_at=_NOW,
        latency_ms=10,
        stripe_intent_id=_INTENT,
        amount=100,
        currency="usd",
        idempotency_key=f"snitchroot-probe-{_PROBE_ID}",
    )


def _webhook(
    created_at: datetime = _NOW,
    stripe_event_id: str = _EVENT,
) -> NormalizedWebhookEvent:
    return NormalizedWebhookEvent(
        stripe_event_id=stripe_event_id,
        event_type=WebhookEventType.PAYMENT_INTENT_SUCCEEDED,
        livemode=False,
        api_version="2024-06-20",
        created_at=created_at,
        object_id=_INTENT,
        object_status="succeeded",
        amount=100,
        currency="usd",
    )


def _matched_report() -> object:
    engine = ReconciliationEngine(window_seconds=3600)
    return engine.reconcile(_probe(), _webhook())


@pytest.mark.asyncio
async def test_settled_balance_transaction_creates_no_discrepancy() -> None:
    bt_client = AsyncMock(spec=BalanceTransactionClient)
    bt_client.check_for_source.return_value = BalanceTransactionCheckResult(
        settled=True,
        net=97,
        bt_id="txn_settled",
    )

    report = _matched_report()
    discrepancy = await verify_settlement_after_match(
        report,
        _webhook(),
        _KEY,
        bt_client,
        settlement_lag_minutes=15,
        now=_NOW + timedelta(minutes=30),
    )

    assert discrepancy is None
    bt_client.check_for_source.assert_awaited_once_with(_KEY, _EVENT)


@pytest.mark.asyncio
async def test_missing_balance_transaction_after_lag_creates_high_discrepancy() -> None:
    bt_client = AsyncMock(spec=BalanceTransactionClient)
    bt_client.check_for_source.return_value = BalanceTransactionCheckResult(
        settled=False,
        net=None,
        bt_id=None,
    )

    report = _matched_report()
    now = _NOW + timedelta(minutes=16)

    discrepancy = await verify_settlement_after_match(
        report,
        _webhook(created_at=_NOW),
        _KEY,
        bt_client,
        settlement_lag_minutes=15,
        now=now,
    )

    assert discrepancy is not None
    assert discrepancy.severity == DiscrepancySeverity.HIGH
    assert "no settled BalanceTransaction" in discrepancy.description


@pytest.mark.asyncio
async def test_missing_bt_before_lag_does_not_create_discrepancy() -> None:
    bt_client = AsyncMock(spec=BalanceTransactionClient)
    bt_client.check_for_source.return_value = BalanceTransactionCheckResult(
        settled=False,
        net=None,
        bt_id=None,
    )

    report = _matched_report()
    discrepancy = await verify_settlement_after_match(
        report,
        _webhook(),
        _KEY,
        bt_client,
        settlement_lag_minutes=15,
        now=_NOW + timedelta(minutes=10),
    )

    assert discrepancy is None


@pytest.mark.asyncio
async def test_stripe_api_error_soft_fails_without_discrepancy() -> None:
    bt_client = AsyncMock(spec=BalanceTransactionClient)
    bt_client.check_for_source.return_value = BalanceTransactionCheckResult(
        settled=False,
        net=None,
        bt_id=None,
        api_error="rate limit",
    )

    report = _matched_report()
    discrepancy = await verify_settlement_after_match(
        report,
        _webhook(),
        _KEY,
        bt_client,
        settlement_lag_minutes=15,
        now=_NOW + timedelta(hours=1),
    )

    assert discrepancy is None


@pytest.mark.asyncio
async def test_reconciliation_job_returns_discrepancy_when_unsettled() -> None:
    bt_client = AsyncMock(spec=BalanceTransactionClient)
    bt_client.check_for_source.return_value = BalanceTransactionCheckResult(
        settled=False,
        net=None,
        bt_id=None,
    )

    item = ReconciliationWorkItem(
        probe=_probe(),
        webhook=_webhook(created_at=_NOW - timedelta(hours=1)),
        stripe_restricted_key=_KEY,
    )
    source = InMemoryReconciliationPairSource([item])

    discrepancies = await run_reconciliation_job(
        source,
        engine=ReconciliationEngine(window_seconds=7200),
        bt_client=bt_client,
        now=_NOW,
    )

    assert len(discrepancies) == 1
    assert discrepancies[0].severity == DiscrepancySeverity.HIGH


@pytest.mark.asyncio
async def test_reconciliation_job_skips_discrepancy_when_settled() -> None:
    bt_client = AsyncMock(spec=BalanceTransactionClient)
    bt_client.check_for_source.return_value = BalanceTransactionCheckResult(
        settled=True,
        net=50,
        bt_id="txn_1",
    )

    item = ReconciliationWorkItem(
        probe=_probe(),
        webhook=_webhook(created_at=_NOW - timedelta(hours=1)),
        stripe_restricted_key=_KEY,
    )
    source = InMemoryReconciliationPairSource([item])

    discrepancies = await run_reconciliation_job(
        source,
        bt_client=bt_client,
        now=_NOW,
    )

    assert discrepancies == []


@pytest.mark.asyncio
async def test_balance_transaction_client_calls_stripe_list() -> None:
    mock_bt = MagicMock()
    mock_bt.id = "txn_abc"
    mock_bt.net = 120

    mock_page = MagicMock()
    mock_page.data = [mock_bt]

    with patch("app.probes.balance_transaction_client.stripe.StripeClient") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.balance_transactions.list.return_value = mock_page

        client = BalanceTransactionClient()
        result = await client.check_for_source(_KEY, _EVENT)

    assert result.settled is True
    assert result.net == 120
    assert result.bt_id == "txn_abc"
    mock_client.balance_transactions.list.assert_called_once_with(
        params={"source": _EVENT, "limit": 1},
    )


@pytest.mark.asyncio
async def test_balance_transaction_client_zero_net_not_settled() -> None:
    mock_bt = MagicMock()
    mock_bt.id = "txn_zero"
    mock_bt.net = 0

    mock_page = MagicMock()
    mock_page.data = [mock_bt]

    with patch("app.probes.balance_transaction_client.stripe.StripeClient") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.balance_transactions.list.return_value = mock_page

        result = await BalanceTransactionClient().check_for_source(_KEY, _EVENT)

    assert result.settled is False
    assert result.net == 0


@pytest.mark.asyncio
async def test_balance_transaction_client_stripe_error_is_soft() -> None:
    with patch("app.probes.balance_transaction_client.stripe.StripeClient") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.balance_transactions.list.side_effect = stripe.StripeError("boom")

        result = await BalanceTransactionClient().check_for_source(_KEY, _EVENT)

    assert result.check_succeeded is False
    assert result.api_error == "boom"


@pytest.mark.asyncio
async def test_reconcile_with_settlement_check_end_to_end() -> None:
    bt_client = AsyncMock(spec=BalanceTransactionClient)
    bt_client.check_for_source.return_value = BalanceTransactionCheckResult(
        settled=True,
        net=1,
        bt_id="txn",
    )

    report, discrepancy = await reconcile_with_settlement_check(
        ReconciliationEngine(window_seconds=3600),
        _probe(),
        _webhook(),
        _KEY,
        bt_client,
        settlement_lag_minutes=15,
        now=_NOW + timedelta(minutes=20),
    )

    assert report.status == ReconciliationStatus.MATCHED
    assert discrepancy is None
