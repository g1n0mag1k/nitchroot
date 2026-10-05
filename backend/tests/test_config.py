"""
Tests for application configuration and settings validation.
"""

import pytest
from app.core.config import ProbeMode, Settings, get_settings
from pydantic import ValidationError


def test_settings_load_from_env(settings_override: None) -> None:
    """Settings must load without error in test environment."""
    settings = get_settings()
    assert settings.environment.value == "test"
    assert settings.probe_mode == ProbeMode.PASSIVE_PRODUCTION


def test_stripe_full_secret_key_rejected() -> None:
    """Full Stripe secret keys must be rejected at settings parse time."""
    with pytest.raises(ValidationError, match="restricted key"):
        Settings(
            database_url="postgresql+asyncpg://u:p@localhost/db",
            redis_url="redis://localhost:6379/0",
            stripe_restricted_key="sk_live_this_should_be_rejected",
            stripe_webhook_secret="whsec_test",
            secret_key="a" * 64,
        )


def test_restricted_key_accepted() -> None:
    """Restricted Stripe keys must be accepted."""
    settings = Settings(
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379/0",
        stripe_restricted_key="rk_live_valid_restricted_key",
        stripe_webhook_secret="whsec_test",
        secret_key="a" * 64,
    )
    assert settings.stripe_restricted_key == "rk_live_valid_restricted_key"


def test_is_production_flag() -> None:
    """is_production must return True only in production environment."""
    settings = Settings(
        database_url="postgresql+asyncpg://u:p@localhost/db",
        redis_url="redis://localhost:6379/0",
        stripe_restricted_key="rk_test_key",
        stripe_webhook_secret="whsec_test",
        secret_key="a" * 64,
        environment="production",
    )
    assert settings.is_production is True
    assert settings.is_test is False
