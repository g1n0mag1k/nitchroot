"""
Tests for PROBE_MODE safety boundary around synthetic Stripe probes.
"""

from datetime import UTC, datetime
from typing import Self
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from app.core.config import ProbeMode, Settings, get_settings
from app.main import create_app, lifespan
from app.probes.executor import SyntheticProbeDisabledError, SyntheticProbeExecutor
from app.probes.models import ProbeRequest, ProbeResult, ProbeStatus
from app.probes.stripe_prober import StripeProber


def _settings(**overrides: object) -> Settings:
    base = {
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "redis_url": "redis://localhost:6379/0",
        "stripe_restricted_key": "rk_test_placeholder",
        "stripe_webhook_secret": "whsec_test",
        "secret_key": "a" * 64,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _probe_request(key: str = "rk_test_merchant_key") -> ProbeRequest:
    return ProbeRequest(
        merchant_id=uuid4(),
        stripe_restricted_key=key,
    )


def _make_probe_result(request: ProbeRequest) -> ProbeResult:
    return ProbeResult(
        probe_id=request.probe_id,
        merchant_id=request.merchant_id,
        status=ProbeStatus.SUCCEEDED,
        latency_ms=12,
        probed_at=datetime.now(tz=UTC),
        stripe_intent_id="pi_test123",
        amount=request.amount,
        currency=request.currency,
        idempotency_key=f"snitchroot-probe-{request.probe_id}",
        raw_response_hash="abc123",
    )


def test_default_probe_mode_is_passive_production(settings_override: None) -> None:
    """PROBE_MODE must default to passive_production."""
    settings = get_settings()
    assert settings.probe_mode == ProbeMode.PASSIVE_PRODUCTION


def test_probe_mode_defaults_without_env_var() -> None:
    """Settings constructed directly must use passive_production by default."""
    settings = _settings()
    assert settings.probe_mode == ProbeMode.PASSIVE_PRODUCTION


def test_live_synthetic_mode_requires_explicit_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live synthetic probes require PROBE_MODE=live_synthetic_review_required."""
    get_settings.cache_clear()
    monkeypatch.delenv("PROBE_MODE", raising=False)
    assert get_settings().probe_mode == ProbeMode.PASSIVE_PRODUCTION

    get_settings.cache_clear()
    monkeypatch.setenv("PROBE_MODE", "live_synthetic_review_required")
    assert get_settings().probe_mode == ProbeMode.LIVE_SYNTHETIC_REVIEW_REQUIRED


@pytest.mark.asyncio
async def test_passive_production_blocks_synthetic_probe() -> None:
    """passive_production must not invoke StripeProber."""
    mock_prober = MagicMock(spec=StripeProber)
    mock_prober.probe = AsyncMock()
    executor = SyntheticProbeExecutor(
        prober=mock_prober,
        settings_factory=lambda: _settings(probe_mode=ProbeMode.PASSIVE_PRODUCTION),
    )

    with pytest.raises(SyntheticProbeDisabledError, match="passive_production"):
        await executor.execute(_probe_request())

    mock_prober.probe.assert_not_called()


@pytest.mark.asyncio
async def test_passive_production_blocks_live_synthetic_probe() -> None:
    """passive_production must block live-key synthetic probes."""
    mock_prober = MagicMock(spec=StripeProber)
    mock_prober.probe = AsyncMock()
    executor = SyntheticProbeExecutor(
        prober=mock_prober,
        settings_factory=lambda: _settings(probe_mode=ProbeMode.PASSIVE_PRODUCTION),
    )

    with pytest.raises(SyntheticProbeDisabledError):
        await executor.execute(_probe_request(key="rk_live_merchant_key"))

    mock_prober.probe.assert_not_called()


@pytest.mark.asyncio
async def test_synthetic_sandbox_invokes_prober_with_test_key() -> None:
    """synthetic_sandbox must delegate to StripeProber for test keys."""
    request = _probe_request()
    expected = _make_probe_result(request)
    mock_prober = MagicMock(spec=StripeProber)
    mock_prober.probe = AsyncMock(return_value=expected)
    executor = SyntheticProbeExecutor(
        prober=mock_prober,
        settings_factory=lambda: _settings(probe_mode=ProbeMode.SYNTHETIC_SANDBOX),
    )

    result = await executor.execute(request)

    assert result == expected
    mock_prober.probe.assert_awaited_once_with(request)


@pytest.mark.asyncio
async def test_synthetic_sandbox_rejects_live_restricted_key() -> None:
    """synthetic_sandbox must not run live synthetic probes."""
    mock_prober = MagicMock(spec=StripeProber)
    mock_prober.probe = AsyncMock()
    executor = SyntheticProbeExecutor(
        prober=mock_prober,
        settings_factory=lambda: _settings(probe_mode=ProbeMode.SYNTHETIC_SANDBOX),
    )

    with pytest.raises(SyntheticProbeDisabledError, match="live"):
        await executor.execute(_probe_request(key="rk_live_merchant_key"))

    mock_prober.probe.assert_not_called()


@pytest.mark.asyncio
async def test_live_synthetic_mode_invokes_prober_for_live_key() -> None:
    """live_synthetic_review_required must allow live-key synthetic probes."""
    request = _probe_request(key="rk_live_merchant_key")
    expected = _make_probe_result(request)
    mock_prober = MagicMock(spec=StripeProber)
    mock_prober.probe = AsyncMock(return_value=expected)
    executor = SyntheticProbeExecutor(
        prober=mock_prober,
        settings_factory=lambda: _settings(
            probe_mode=ProbeMode.LIVE_SYNTHETIC_REVIEW_REQUIRED,
        ),
    )

    result = await executor.execute(request)

    assert result == expected
    mock_prober.probe.assert_awaited_once_with(request)


@pytest.mark.asyncio
async def test_startup_logs_probe_mode(settings_override: None) -> None:
    """Startup must log the active probe mode unambiguously."""
    recorded: list[tuple[str, dict[str, object]]] = []

    class RecordingLogger:
        def info(self, event: str, **kwargs: object) -> None:
            recorded.append((event, kwargs))

        def bind(self, **_kwargs: object) -> Self:
            return self

        def exception(self, *_args: object, **_kwargs: object) -> None:
            pass

    import app.main as main_module

    original_logger = main_module.logger
    main_module.logger = RecordingLogger()
    try:
        app = create_app()
        async with lifespan(app):
            pass
    finally:
        main_module.logger = original_logger

    starting = [entry for entry in recorded if entry[0] == "snitchroot_starting"]
    assert len(starting) == 1
    assert starting[0][1]["probe_mode"] == ProbeMode.PASSIVE_PRODUCTION.value
