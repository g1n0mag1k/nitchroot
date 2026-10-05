"""
Tests for the zero-fee Stripe probe engine.

All Stripe API calls are mocked — no real network calls are made.
Tests cover the full probe lifecycle including every failure mode.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import stripe

from app.probes.models import ProbeFailureMode, ProbeRequest, ProbeStatus
from app.probes.stripe_prober import StripeProber, _sha256, _validate_intent_schema


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _make_request(**kwargs: object) -> ProbeRequest:
    """Build a ProbeRequest with safe test defaults."""
    defaults = {
        "merchant_id": uuid4(),
        "stripe_restricted_key": "rk_test_placeholder",
    }
    defaults.update(kwargs)
    return ProbeRequest(**defaults)  # type: ignore[arg-type]


def _mock_intent(
    intent_id: str = "pi_test_123",
    status: str = "requires_payment_method",
    client_secret: str = "pi_test_123_secret_abc",
    probe_id: str | None = None,
    merchant_id: str | None = None,
) -> MagicMock:
    """Build a mock Stripe PaymentIntent object."""
    intent = MagicMock(spec=stripe.PaymentIntent)
    intent.id = intent_id
    intent.status = status
    intent.client_secret = client_secret
    intent.metadata = {
        "probe_id": probe_id or str(uuid4()),
        "merchant_id": merchant_id or str(uuid4()),
        "source": "snitchroot",
    }
    return intent


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_probe_succeeds_on_valid_response() -> None:
    """Probe must return SUCCEEDED when Stripe responds correctly."""
    request = _make_request()
    intent = _mock_intent(
        probe_id=str(request.probe_id),
        merchant_id=str(request.merchant_id),
    )

    with patch("app.probes.stripe_prober.stripe.StripeClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.payment_intents.create.return_value = intent
        mock_client.payment_intents.cancel.return_value = MagicMock()

        prober = StripeProber()
        result = await prober.probe(request)

    assert result.succeeded
    assert result.status == ProbeStatus.SUCCEEDED
    assert result.stripe_intent_id == "pi_test_123"
    assert result.failure_mode is None
    assert result.error_message is None
    assert result.latency_ms >= 0
    assert result.raw_response_hash == _sha256("pi_test_123")


@pytest.mark.asyncio
async def test_probe_sets_correct_merchant_and_probe_ids() -> None:
    """ProbeResult IDs must match the ProbeRequest exactly."""
    request = _make_request()
    intent = _mock_intent(
        probe_id=str(request.probe_id),
        merchant_id=str(request.merchant_id),
    )

    with patch("app.probes.stripe_prober.stripe.StripeClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.payment_intents.create.return_value = intent
        mock_client.payment_intents.cancel.return_value = MagicMock()

        result = await StripeProber().probe(request)

    assert result.probe_id == request.probe_id
    assert result.merchant_id == request.merchant_id


@pytest.mark.asyncio
async def test_probe_calls_cancel_with_abandoned_reason() -> None:
    """Probe must cancel the intent with cancellation_reason='abandoned'."""
    request = _make_request()
    intent = _mock_intent(
        probe_id=str(request.probe_id),
        merchant_id=str(request.merchant_id),
    )

    with patch("app.probes.stripe_prober.stripe.StripeClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.payment_intents.create.return_value = intent
        mock_client.payment_intents.cancel.return_value = MagicMock()

        await StripeProber().probe(request)

    mock_client.payment_intents.cancel.assert_called_once_with(
        "pi_test_123",
        params={"cancellation_reason": "abandoned"},
    )


# --------------------------------------------------------------------------- #
# Auth failure
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_probe_fails_on_authentication_error() -> None:
    """Auth failure must return AUTH_ERROR failure mode."""
    request = _make_request()

    with patch("app.probes.stripe_prober.stripe.StripeClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.payment_intents.create.side_effect = stripe.AuthenticationError(
            "No such API key"
        )

        result = await StripeProber().probe(request)

    assert result.failed
    assert result.failure_mode == ProbeFailureMode.AUTH_ERROR
    assert result.stripe_intent_id is None


# --------------------------------------------------------------------------- #
# Creation failure
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_probe_fails_on_api_error_during_creation() -> None:
    """Generic Stripe API error during creation maps to CREATION_ERROR."""
    request = _make_request()

    with patch("app.probes.stripe_prober.stripe.StripeClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.payment_intents.create.side_effect = stripe.APIConnectionError(
            "Connection refused"
        )

        result = await StripeProber().probe(request)

    assert result.failed
    assert result.failure_mode == ProbeFailureMode.CREATION_ERROR


# --------------------------------------------------------------------------- #
# Schema drift
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_probe_fails_on_unexpected_intent_status() -> None:
    """Wrong intent status must trigger SCHEMA_DRIFT failure."""
    request = _make_request()
    intent = _mock_intent(
        status="requires_confirmation",  # Not the expected status
        probe_id=str(request.probe_id),
        merchant_id=str(request.merchant_id),
    )

    with patch("app.probes.stripe_prober.stripe.StripeClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.payment_intents.create.return_value = intent
        mock_client.payment_intents.cancel.return_value = MagicMock()

        result = await StripeProber().probe(request)

    assert result.failed
    assert result.failure_mode == ProbeFailureMode.SCHEMA_DRIFT
    assert "requires_confirmation" in (result.error_message or "")


@pytest.mark.asyncio
async def test_probe_fails_on_missing_client_secret() -> None:
    """Missing client_secret must trigger SCHEMA_DRIFT failure."""
    request = _make_request()
    intent = _mock_intent(
        client_secret="",
        probe_id=str(request.probe_id),
        merchant_id=str(request.merchant_id),
    )

    with patch("app.probes.stripe_prober.stripe.StripeClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.payment_intents.create.return_value = intent
        mock_client.payment_intents.cancel.return_value = MagicMock()

        result = await StripeProber().probe(request)

    assert result.failed
    assert result.failure_mode == ProbeFailureMode.SCHEMA_DRIFT


@pytest.mark.asyncio
async def test_probe_fails_on_metadata_mismatch() -> None:
    """Metadata round-trip failure must trigger SCHEMA_DRIFT."""
    request = _make_request()
    intent = _mock_intent(
        probe_id="wrong-probe-id",  # Does not match request.probe_id
        merchant_id=str(request.merchant_id),
    )

    with patch("app.probes.stripe_prober.stripe.StripeClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.payment_intents.create.return_value = intent
        mock_client.payment_intents.cancel.return_value = MagicMock()

        result = await StripeProber().probe(request)

    assert result.failed
    assert result.failure_mode == ProbeFailureMode.SCHEMA_DRIFT


# --------------------------------------------------------------------------- #
# Cancel failure
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_probe_fails_on_cancel_error() -> None:
    """Cancel failure must return CANCEL_ERROR failure mode."""
    request = _make_request()
    intent = _mock_intent(
        probe_id=str(request.probe_id),
        merchant_id=str(request.merchant_id),
    )

    with patch("app.probes.stripe_prober.stripe.StripeClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.payment_intents.create.return_value = intent
        mock_client.payment_intents.cancel.side_effect = stripe.APIConnectionError(
            "Timeout cancelling intent"
        )

        result = await StripeProber().probe(request)

    assert result.failed
    assert result.failure_mode == ProbeFailureMode.CANCEL_ERROR
    assert result.stripe_intent_id == "pi_test_123"


# --------------------------------------------------------------------------- #
# Unit tests for helpers
# --------------------------------------------------------------------------- #


def test_sha256_is_deterministic() -> None:
    """SHA-256 helper must return the same digest for the same input."""
    assert _sha256("pi_test") == _sha256("pi_test")


def test_sha256_differs_for_different_inputs() -> None:
    """SHA-256 helper must return different digests for different inputs."""
    assert _sha256("pi_test_1") != _sha256("pi_test_2")


def test_validate_intent_schema_passes_valid_intent() -> None:
    """Schema validator must return None for a well-formed intent."""
    request = _make_request()
    intent = _mock_intent(
        probe_id=str(request.probe_id),
        merchant_id=str(request.merchant_id),
    )
    assert _validate_intent_schema(intent, request) is None


def test_validate_intent_schema_catches_bad_id_prefix() -> None:
    """Schema validator must catch an intent ID that doesn't start with pi_."""
    request = _make_request()
    intent = _mock_intent(
        intent_id="ch_not_a_payment_intent",
        probe_id=str(request.probe_id),
        merchant_id=str(request.merchant_id),
    )
    error = _validate_intent_schema(intent, request)
    assert error is not None
    assert "pi_" in error
