"""Tests for crypto helpers and the APScheduler probe worker."""

from __future__ import annotations

import sys
import types
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from app.core.config import get_settings
from app.core.crypto import decrypt_bytes, encrypt_string
from app.probes.models import ProbeResult, ProbeStatus


def _install_merchant_models() -> None:
    """Register minimal ORM stubs expected by workers.runner (merchant.py)."""
    if "app.models.merchant" in sys.modules:
        return

    models_pkg = types.ModuleType("app.models")
    merchant_mod = types.ModuleType("app.models.merchant")

    class Merchant:
        __tablename__ = "merchants"

        def __init__(
            self,
            *,
            id: uuid.UUID,
            name: str,
            stripe_restricted_key_enc: bytes,
            probe_interval_seconds: int,
        ) -> None:
            self.id = id
            self.name = name
            self.stripe_restricted_key_enc = stripe_restricted_key_enc
            self.probe_interval_seconds = probe_interval_seconds

    class ProbeResultRecord:
        __tablename__ = "probe_results"

        def __init__(self, **kwargs: object) -> None:
            for key, value in kwargs.items():
                setattr(self, key, value)

    merchant_mod.Merchant = Merchant
    merchant_mod.ProbeResultRecord = ProbeResultRecord
    sys.modules["app.models"] = models_pkg
    sys.modules["app.models.merchant"] = merchant_mod


_install_merchant_models()

_KEY_HEX = "a" * 64


def _runner():
    from workers import runner

    return runner


def test_encrypt_decrypt_roundtrip() -> None:
    """Plaintext survives encrypt → decrypt."""
    plaintext = "rk_test_restricted_key"
    ciphertext = encrypt_string(plaintext, _KEY_HEX)
    assert decrypt_bytes(ciphertext, _KEY_HEX) == plaintext


def test_encrypt_uses_random_nonce() -> None:
    """Each encryption call must emit distinct ciphertext."""
    plaintext = "same-secret"
    first = encrypt_string(plaintext, _KEY_HEX)
    second = encrypt_string(plaintext, _KEY_HEX)
    assert first != second
    assert decrypt_bytes(first, _KEY_HEX) == plaintext
    assert decrypt_bytes(second, _KEY_HEX) == plaintext


@pytest.mark.asyncio
async def test_probe_skipped_when_lock_not_acquired() -> None:
    """Worker must not probe when Redis SET NX fails (lock held)."""
    merchant_id = uuid.uuid4()
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=None)

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(
        return_value=MagicMock(
            id=merchant_id,
            stripe_restricted_key_enc=b"enc",
            probe_interval_seconds=300,
        )
    )
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_session
    mock_cm.__aexit__.return_value = None

    runner = _runner()
    with (
        patch("workers.runner.AsyncSessionLocal", return_value=mock_cm),
        patch("workers.runner.StripeProber") as prober_cls,
    ):
        await runner.execute_merchant_probe(merchant_id, redis=redis)

    redis.set.assert_awaited_once()
    prober_cls.return_value.probe.assert_not_called()


@pytest.mark.asyncio
async def test_probe_result_persisted_to_db() -> None:
    """Successful probes must be written to probe_results via SQLAlchemy."""
    merchant_id = uuid.uuid4()
    probe_id = uuid.uuid4()
    settings = get_settings()
    encrypted_key = encrypt_string("rk_test_persist", settings.secret_key)

    probe_result = ProbeResult(
        probe_id=probe_id,
        merchant_id=merchant_id,
        status=ProbeStatus.SUCCEEDED,
        latency_ms=12,
        probed_at=datetime.now(tz=UTC),
        stripe_intent_id="pi_test",
        idempotency_key=f"snitchroot-probe-{probe_id}",
        raw_response_hash="abc123",
    )

    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)

    mock_session = AsyncMock()
    mock_session.get = AsyncMock(
        return_value=MagicMock(
            id=merchant_id,
            stripe_restricted_key_enc=encrypted_key,
            probe_interval_seconds=600,
        )
    )
    mock_cm = AsyncMock()
    mock_cm.__aenter__.return_value = mock_session
    mock_cm.__aexit__.return_value = None

    prober = MagicMock()
    prober.probe = AsyncMock(return_value=probe_result)

    runner = _runner()
    with patch("workers.runner.AsyncSessionLocal", return_value=mock_cm):
        await runner.execute_merchant_probe(merchant_id, redis=redis, prober=prober)

    mock_session.add.assert_called_once()
    added = mock_session.add.call_args[0][0]
    expected = runner.probe_result_to_record(probe_result)
    assert added.id == expected.id
    assert added.merchant_id == merchant_id
    assert added.status == ProbeStatus.SUCCEEDED.value
    assert added.stripe_intent_id == "pi_test"
    mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_try_acquire_probe_lock_ttl() -> None:
    """Lock TTL is interval minus five seconds."""
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    merchant_id = uuid.uuid4()

    runner = _runner()
    assert await runner.try_acquire_probe_lock(redis, merchant_id, 120) is True
    redis.set.assert_awaited_with(
        f"snitchroot:probe_lock:{merchant_id}",
        "1",
        nx=True,
        ex=115,
    )
