"""Ledger reconciliation engine.

Compares ProbeResult observations against NormalizedWebhookEvents within a
configurable sliding time window and produces immutable ReconciliationReports.

No raw Stripe payloads are stored. All comparisons use derived, non-PII fields.
"""

from __future__ import annotations

from datetime import timedelta

import structlog

from app.probes.models import ProbeResult
from app.webhooks.models import NormalizedWebhookEvent

from .models import (
    DiscrepancyField,
    ReconciliationReport,
    ReconciliationStatus,
)

log = structlog.get_logger(__name__)

_DEFAULT_WINDOW_SECONDS = 300  # 5 minutes


class ReconciliationEngine:
    """Matches probe records against webhook events using a sliding time window."""

    def __init__(self, window_seconds: int = _DEFAULT_WINDOW_SECONDS) -> None:
        """Initialise the engine with a sliding match window.

        Args:
            window_seconds: Maximum absolute seconds between probe and webhook
                timestamps for a pair to be considered matchable.
        """
        self._window = timedelta(seconds=window_seconds)

    def reconcile(
        self,
        probe: ProbeResult | None,
        webhook: NormalizedWebhookEvent | None,
    ) -> ReconciliationReport:
        """Produce a ReconciliationReport from a probe/webhook pair.

        Args:
            probe: The ProbeResult from our zero-fee probe, or None.
            webhook: The NormalizedWebhookEvent from Stripe, or None.

        Returns:
            An immutable ReconciliationReport describing the match outcome.

        Raises:
            ValueError: If both probe and webhook are None.
        """
        if probe is None and webhook is None:
            raise ValueError("At least one of probe or webhook must be provided")

        # ── PROBE_ONLY: probe fired but no webhook arrived ──────────────────
        if probe is not None and webhook is None:
            log.warning(
                "reconciliation_probe_only",
                probe_id=str(probe.probe_id),
                merchant_id=str(probe.merchant_id),
            )
            return ReconciliationReport(
                merchant_id=probe.merchant_id,
                probe_id=probe.probe_id,
                stripe_intent_id=probe.stripe_intent_id,
                status=ReconciliationStatus.PROBE_ONLY,
                probe_amount=probe.amount,
                probe_currency=probe.currency,
                window_seconds=int(self._window.total_seconds()),
            )

        # ── WEBHOOK_ONLY: event arrived but no probe record ─────────────────
        if webhook is not None and probe is None:
            log.warning(
                "reconciliation_webhook_only",
                stripe_event_id=webhook.stripe_event_id,
            )
            return ReconciliationReport(
                stripe_event_id=webhook.stripe_event_id,
                stripe_intent_id=webhook.object_id,
                status=ReconciliationStatus.WEBHOOK_ONLY,
                webhook_amount=webhook.amount,
                webhook_currency=webhook.currency,
                window_seconds=int(self._window.total_seconds()),
            )

        # ── Both present — check window then compare fields ─────────────────
        assert probe is not None and webhook is not None  # mypy narrowing

        # Window check: webhook must arrive within window of probe
        time_delta = abs(webhook.created_at - probe.probed_at)
        if time_delta > self._window:
            log.warning(
                "reconciliation_outside_window",
                probe_id=str(probe.probe_id),
                stripe_event_id=webhook.stripe_event_id,
                delta_seconds=time_delta.total_seconds(),
                window_seconds=self._window.total_seconds(),
            )
            return ReconciliationReport(
                merchant_id=probe.merchant_id,
                probe_id=probe.probe_id,
                stripe_event_id=webhook.stripe_event_id,
                stripe_intent_id=probe.stripe_intent_id,
                status=ReconciliationStatus.PROBE_ONLY,
                probe_amount=probe.amount,
                probe_currency=probe.currency,
                webhook_amount=webhook.amount,
                webhook_currency=webhook.currency,
                window_seconds=int(self._window.total_seconds()),
            )

        discrepancies: list[DiscrepancyField] = []

        if probe.amount != webhook.amount:
            discrepancies.append(DiscrepancyField.AMOUNT)
        if probe.currency != webhook.currency:
            discrepancies.append(DiscrepancyField.CURRENCY)

        status = (
            ReconciliationStatus.DISCREPANCY if discrepancies else ReconciliationStatus.MATCHED
        )

        report = ReconciliationReport(
            merchant_id=probe.merchant_id,
            probe_id=probe.probe_id,
            stripe_event_id=webhook.stripe_event_id,
            stripe_intent_id=probe.stripe_intent_id,
            status=status,
            discrepancies=discrepancies,
            probe_amount=probe.amount,
            webhook_amount=webhook.amount,
            probe_currency=probe.currency,
            webhook_currency=webhook.currency,
            window_seconds=int(self._window.total_seconds()),
        )

        log.info(
            "reconciliation_complete",
            probe_id=str(probe.probe_id),
            stripe_event_id=webhook.stripe_event_id,
            status=status,
            discrepancy_count=len(discrepancies),
        )
        return report
