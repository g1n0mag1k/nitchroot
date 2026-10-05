"""Tests for crypto helpers and the APScheduler probe worker."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# workers.runner imports the DB engine at module load; mirror conftest env.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://snitchroot:snitchroot_test@localhost:5432/snitchroot_test",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("STRIPE_RESTRICTED_KEY", "rk_test_placeholder_do_not_use")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_placeholder_do_not_use")
os.environ.setdefault("SECRET_KEY", "a" * 64)

from app.core.crypto import decrypt_bytes, encrypt_string
from app.probes.models import ProbeRequest, ProbeResult, ProbeStatus
from workers.runner import execute_merchant_probe, persist_probe_result

_TEST_KEY_HEX = "a" * 64


def test_encrypt_decrypt_round_trip() -> None:
    """Ciphertext must decrypt back to the original plaintext."""
    plaintext = "rk_test_snitchroot_key"
    ciphertext = encrypt_string(plaintext, _TEST_KEY_HEX)
    assert decrypt_bytes(ciphertext, _TEST_KEY_HEX) == plaintext


def test_encrypt_uses_random_nonce() -> None:
    """Each encryption must use a fresh nonce (distinct ciphertexts)."""
    plaintext = "same-plaintext"
    first = encrypt_string(plaintext, _TEST_KEY_HEX)
    second = encrypt_string(plaintext, _TEST_KEY_HEX)
    assert first != second
    assert decrypt_bytes(first, _TEST_KEY_HEX) == plaintext
    assert decrypt_bytes(second, _TEST_KEY_HEX) == plaintext


@pytest.mark.asyncio
async def test_probe_skipped_when_redis_lock_not_acquired() -> None:
    """Worker must not probe when the distributed lock is already held."""
    merchant_id = uuid4()
    mock_redis = AsyncMock()
    mock_redis.set.return_value = None

    merchant = SimpleNamespace(
        id=merchant_id,
        probe_interval_seconds=900,
        stripe_restricted_key_enc=b"enc",
    )
    mock_session = AsyncMock()
    mock_session.get.return_value = merchant
    mock_session.__aenter__.return_value = mock_session
    mock_session.__aexit__.return_value = None

    mock_factory = MagicMock()
    mock_factory.return_value = mock_session

    with patch("workers.runner.StripeProber") as mock_prober_cls:
        await execute_merchant_probe(
            merchant_id,
            redis=mock_redis,
            session_factory=mock_factory,
        )

    mock_redis.set.assert_awaited_once()
    mock_prober_cls.assert_not_called()


@pytest.mark.asyncio
async def test_probe_result_persisted_to_database() -> None:
    """A successful StripeProber result must be written to probe_results."""
    merchant_id = uuid4()
    probe_id = uuid4()
    encrypted_key = encrypt_string("rk_test_persist", _TEST_KEY_HEX)

    merchant = SimpleNamespace(
        id=merchant_id,
        probe_interval_seconds=120,
        stripe_restricted_key_enc=encrypted_key,
    )

    probe_result = ProbeResult(
        probe_id=probe_id,
        merchant_id=merchant_id,
        status=ProbeStatus.SUCCEEDED,
        latency_ms=42,
        probed_at=datetime.now(tz=UTC),
        stripe_intent_id="pi_test_persist",
        idempotency_key=f"snitchroot-probe-{probe_id}",
        raw_response_hash="abc123",
    )

    mock_redis = AsyncMock()
    mock_redis.set.return_value = True

    mock_session = AsyncMock()
    mock_session.get.return_value = merchant
    mock_session.__aenter__.return_value = mock_session
    mock_session.__aexit__.return_value = None

    mock_factory = MagicMock()
    mock_factory.return_value = mock_session

    mock_prober = AsyncMock()
    mock_prober.probe.return_value = probe_result

    with (
        patch("workers.runner.get_settings") as mock_settings,
        patch("workers.runner.StripeProber", return_value=mock_prober),
        patch("workers.runner.persist_probe_result", new_callable=AsyncMock) as mock_persist,
    ):
        settings = MagicMock()
        settings.secret_key = _TEST_KEY_HEX
        settings.probe_timeout_seconds = 30
        mock_settings.return_value = settings

        await execute_merchant_probe(
            merchant_id,
            redis=mock_redis,
            session_factory=mock_factory,
            prober_factory=lambda: mock_prober,
        )

    mock_prober.probe.assert_awaited_once()
    call_request = mock_prober.probe.await_args.args[0]
    assert isinstance(call_request, ProbeRequest)
    assert call_request.merchant_id == merchant_id
    assert call_request.stripe_restricted_key == "rk_test_persist"

    mock_persist.assert_awaited_once()
    persist_session, persisted = mock_persist.await_args.args
    assert persist_session is mock_session
    assert persisted == probe_result


@pytest.mark.asyncio
async def test_persist_probe_result_adds_row() -> None:
    """persist_probe_result must map domain result fields onto the ORM row."""
    merchant_id = uuid4()
    probe_id = uuid4()
    result = ProbeResult(
        probe_id=probe_id,
        merchant_id=merchant_id,
        status=ProbeStatus.SUCCEEDED,
        latency_ms=10,
        probed_at=datetime(2026, 1, 1, tzinfo=UTC),
        stripe_intent_id="pi_row",
        idempotency_key="idem-1",
        raw_response_hash="hash",
    )

    mock_session = MagicMock()
    mock_session.commit = AsyncMock()

    with patch("app.models.probe_result.ProbeResultRow") as mock_row_cls:
        mock_row = MagicMock()
        mock_row_cls.return_value = mock_row

        await persist_probe_result(mock_session, result)

    mock_row_cls.assert_called_once_with(
        id=probe_id,
        merchant_id=merchant_id,
        status="succeeded",
        failure_mode=None,
        stripe_intent_id="pi_row",
        idempotency_key="idem-1",
        latency_ms=10,
        raw_response_hash="hash",
        probed_at=result.probed_at,
    )
    mock_session.add.assert_called_once_with(mock_row)
    mock_session.commit.assert_awaited_once()
