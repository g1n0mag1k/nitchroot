"""
Zero-fee Stripe gateway probe engine.

Validates Stripe gateway health by executing a full PaymentIntent
create → schema validate → cancel lifecycle using a restricted API key.

Cost: $0.00 per probe cycle.
  - PaymentIntent is created with confirm=False (no payment method attached).
  - PaymentIntent is immediately cancelled with cancellation_reason='abandoned'.
  - Stripe only charges fees on capture or confirm with a payment method.

Security:
  - Only restricted keys are accepted (validated at Settings level).
  - Raw API responses are never stored — only their SHA-256 hash.
  - No cardholder data (CHD) is ever handled.

Usage:
    prober = StripeProber()
    result = await prober.probe(ProbeRequest(
        merchant_id=uuid4(),
        stripe_restricted_key="rk_live_...",
    ))
"""

import hashlib
import time
from datetime import UTC, datetime
from typing import cast

import stripe
from stripe import PaymentIntent, StripeError
from structlog.types import FilteringBoundLogger

from app.core.logging import get_logger
from app.probes.models import (
    ProbeFailureMode,
    ProbeRequest,
    ProbeResult,
    ProbeStatus,
)

logger = get_logger(__name__)

# Expected schema invariants for a freshly created PaymentIntent.
# If Stripe changes these, we surface it as schema_drift immediately.
_EXPECTED_STATUS = "requires_payment_method"
_EXPECTED_ID_PREFIX = "pi_"
_REQUIRED_FIELDS = ("id", "status", "client_secret", "metadata")


class StripeProber:
    """
    Executes zero-fee Stripe gateway health probes.

    Each probe:
      1. Creates a PaymentIntent (confirm=False, no payment method).
      2. Validates the response schema against known invariants.
      3. Cancels the intent with cancellation_reason='abandoned'.
      4. Returns an immutable ProbeResult.

    The prober is stateless and safe to instantiate once and reuse
    across concurrent probe executions.
    """

    def __init__(self, http_client: stripe.HTTPClient | None = None) -> None:
        """
        Initialise the prober.

        Args:
            http_client: Optional Stripe HTTP client override, used in
                tests to inject a mock transport without real network calls.
        """
        self._http_client = http_client

    async def probe(self, request: ProbeRequest) -> ProbeResult:
        """
        Execute a full zero-fee probe cycle against the Stripe API.

        Args:
            request: Probe parameters including merchant ID and API key.

        Returns:
            An immutable ProbeResult describing the outcome.
        """
        probe_start = time.monotonic()
        probed_at = datetime.now(tz=UTC)
        idempotency_key = f"snitchroot-probe-{request.probe_id}"

        log = logger.bind(
            probe_id=str(request.probe_id),
            merchant_id=str(request.merchant_id),
            idempotency_key=idempotency_key,
        )
        log.info("probe_started")

        # ------------------------------------------------------------------ #
        # Step 1: Configure a per-request Stripe client with the merchant key.
        # We never mutate the global stripe.api_key — each probe is isolated.
        # ------------------------------------------------------------------ #
        client = stripe.StripeClient(
            api_key=request.stripe_restricted_key,
            http_client=self._http_client,
        )

        # ------------------------------------------------------------------ #
        # Step 2: Create a PaymentIntent (confirm=False, no payment method).
        # ------------------------------------------------------------------ #
        intent_id: str | None = None
        raw_hash: str | None = None

        try:
            intent = client.payment_intents.create(
                params={
                    "amount": request.amount,
                    "currency": request.currency,
                    "confirm": False,
                    "metadata": {
                        "probe_id": str(request.probe_id),
                        "merchant_id": str(request.merchant_id),
                        "source": "snitchroot",
                    },
                },
                options={"idempotency_key": idempotency_key},
            )
        except StripeError as exc:
            return self._build_failure(
                request=request,
                probed_at=probed_at,
                start=probe_start,
                mode=_classify_stripe_error(exc),
                error=str(exc),
                log=log,
            )

        # Hash the intent ID as a proxy for the response (no raw body stored).
        raw_hash = _sha256(intent.id)
        intent_id = intent.id

        # ------------------------------------------------------------------ #
        # Step 3: Validate response schema invariants.
        # ------------------------------------------------------------------ #
        schema_error = _validate_intent_schema(intent, request)
        if schema_error:
            log.warning("probe_schema_drift", detail=schema_error)
            # Still attempt cancellation before returning failure.
            _safe_cancel(client, intent.id, idempotency_key, log)
            return self._build_failure(
                request=request,
                probed_at=probed_at,
                start=probe_start,
                mode=ProbeFailureMode.SCHEMA_DRIFT,
                error=schema_error,
                stripe_intent_id=intent_id,
                idempotency_key=idempotency_key,
                raw_response_hash=raw_hash,
                log=log,
            )

        # ------------------------------------------------------------------ #
        # Step 4: Cancel the intent immediately.
        # cancellation_reason='abandoned' signals this was never a real charge.
        # ------------------------------------------------------------------ #
        try:
            client.payment_intents.cancel(
                intent.id,
                params={"cancellation_reason": "abandoned"},
            )
        except StripeError as exc:
            log.error("probe_cancel_failed", error=str(exc))
            return self._build_failure(
                request=request,
                probed_at=probed_at,
                start=probe_start,
                mode=ProbeFailureMode.CANCEL_ERROR,
                error=str(exc),
                stripe_intent_id=intent_id,
                idempotency_key=idempotency_key,
                raw_response_hash=raw_hash,
                log=log,
            )

        latency_ms = int((time.monotonic() - probe_start) * 1000)
        log.info("probe_succeeded", latency_ms=latency_ms, intent_id=intent_id)

        return ProbeResult(
            probe_id=request.probe_id,
            merchant_id=request.merchant_id,
            status=ProbeStatus.SUCCEEDED,
            latency_ms=latency_ms,
            probed_at=probed_at,
            stripe_intent_id=intent_id,
            amount=request.amount,
            currency=request.currency,
            idempotency_key=idempotency_key,
            raw_response_hash=raw_hash,
        )

    @staticmethod
    def _build_failure(
        request: ProbeRequest,
        probed_at: datetime,
        start: float,
        mode: ProbeFailureMode,
        error: str,
        log: FilteringBoundLogger,
        stripe_intent_id: str | None = None,
        idempotency_key: str | None = None,
        raw_response_hash: str | None = None,
    ) -> ProbeResult:
        """Construct a failed ProbeResult and emit a structured log."""
        latency_ms = int((time.monotonic() - start) * 1000)
        log.error("probe_failed", failure_mode=mode, error=error)
        return ProbeResult(
            probe_id=request.probe_id,
            merchant_id=request.merchant_id,
            status=ProbeStatus.FAILED,
            latency_ms=latency_ms,
            probed_at=probed_at,
            stripe_intent_id=stripe_intent_id,
            amount=request.amount,
            currency=request.currency,
            idempotency_key=idempotency_key,
            raw_response_hash=raw_response_hash,
            failure_mode=mode,
            error_message=error,
        )


# --------------------------------------------------------------------------- #
# Private helpers
# --------------------------------------------------------------------------- #


def _validate_intent_schema(
    intent: PaymentIntent,
    request: ProbeRequest,
) -> str | None:
    """
    Validate that a freshly created PaymentIntent matches known invariants.

    Returns None on success, or a human-readable error string on failure.
    """
    if not intent.id.startswith(_EXPECTED_ID_PREFIX):
        return f"intent.id '{intent.id}' does not start with '{_EXPECTED_ID_PREFIX}'"

    if intent.status != _EXPECTED_STATUS:
        return f"intent.status is '{intent.status}', expected '{_EXPECTED_STATUS}'"

    if not intent.client_secret:
        return "intent.client_secret is missing or empty"

    # Verify metadata round-trip — confirms the key has write access.
    raw_meta = cast(dict[str, str], intent.metadata or {})
    probe_id_value = raw_meta.get("probe_id")
    if probe_id_value != str(request.probe_id):
        return f"metadata.probe_id mismatch: got '{probe_id_value}', expected '{request.probe_id}'"

    return None


def _classify_stripe_error(exc: StripeError) -> ProbeFailureMode:
    """Map a StripeError subtype to a ProbeFailureMode."""
    from stripe import AuthenticationError

    if isinstance(exc, AuthenticationError):
        return ProbeFailureMode.AUTH_ERROR
    return ProbeFailureMode.CREATION_ERROR


def _safe_cancel(
    client: stripe.StripeClient,
    intent_id: str,
    idempotency_key: str,
    log: FilteringBoundLogger,
) -> None:
    """
    Attempt to cancel a PaymentIntent, swallowing any errors.

    Used during schema validation failures to clean up the intent
    before returning — a best-effort operation.
    """
    try:
        client.payment_intents.cancel(
            intent_id,
            params={"cancellation_reason": "abandoned"},
        )
    except StripeError as exc:
        log.warning("probe_cleanup_cancel_failed", error=str(exc))


def _sha256(value: str) -> str:
    """Return the SHA-256 hex digest of a UTF-8 string."""
    digest: str = hashlib.sha256(value.encode()).hexdigest()
    return digest
