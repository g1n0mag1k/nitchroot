"""Stripe BalanceTransaction lookup for ledger settlement verification."""

from __future__ import annotations

from dataclasses import dataclass

import stripe
from stripe import StripeError

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class BalanceTransactionCheckResult:
    """Outcome of a balance transaction list call for a payment source."""

    settled: bool
    net: int | None
    bt_id: str | None
    api_error: str | None = None

    @property
    def check_succeeded(self) -> bool:
        """True when the Stripe API call completed without error."""
        return self.api_error is None


class BalanceTransactionClient:
    """
    Fetches BalanceTransaction records for a Stripe source ID.

    Uses the merchant restricted key (Balance:read scope). Injectable HTTP
    client enables unit tests without network access.
    """

    def __init__(self, http_client: stripe.HTTPClient | None = None) -> None:
        self._http_client = http_client

    async def check_for_source(
        self,
        restricted_key: str,
        source_id: str,
    ) -> BalanceTransactionCheckResult:
        """
        List balance transactions for ``source_id`` and report settlement state.

        A transaction is considered settled when the first returned row has
        ``net > 0``. Stripe API failures are returned as ``api_error`` (soft
        fail) rather than raised.
        """
        client = stripe.StripeClient(
            api_key=restricted_key,
            http_client=self._http_client,
        )
        try:
            page = client.balance_transactions.list(
                params={"source": source_id, "limit": 1},
            )
        except StripeError as exc:
            logger.warning(
                "balance_transaction_check_failed",
                source_id=source_id,
                error=str(exc),
            )
            return BalanceTransactionCheckResult(
                settled=False,
                net=None,
                bt_id=None,
                api_error=str(exc),
            )

        if not page.data:
            return BalanceTransactionCheckResult(
                settled=False,
                net=None,
                bt_id=None,
            )

        bt = page.data[0]
        net = int(bt.net)
        return BalanceTransactionCheckResult(
            settled=net > 0,
            net=net,
            bt_id=bt.id,
        )
