"""
Application configuration via pydantic-settings.

All settings are loaded from environment variables. No secrets are
ever hardcoded. See .env.example for required variables.
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Valid deployment environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    """Valid log levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ProbeMode(StrEnum):
    """
    Operational mode for synthetic PaymentIntent probes.

    passive_production is the safe default: no synthetic intents are executed.
    """

    PASSIVE_PRODUCTION = "passive_production"
    SYNTHETIC_SANDBOX = "synthetic_sandbox"
    LIVE_SYNTHETIC_REVIEW_REQUIRED = "live_synthetic_review_required"


class Settings(BaseSettings):
    """
    SnitchRoot application settings.

    Loaded from environment variables with no defaults for secrets,
    ensuring misconfigured deployments fail fast at startup.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ #
    # Application
    # ------------------------------------------------------------------ #
    app_name: str = "SnitchRoot"
    app_version: str = "0.1.0"
    environment: Environment = Environment.DEVELOPMENT
    log_level: LogLevel = LogLevel.INFO
    debug: bool = False

    # ------------------------------------------------------------------ #
    # Database
    # ------------------------------------------------------------------ #
    database_url: PostgresDsn = Field(
        ...,
        description="Async PostgreSQL DSN (postgresql+asyncpg://...)",
    )
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=20, ge=0, le=100)
    db_pool_timeout: int = Field(default=30, ge=1)
    db_echo: bool = False

    # ------------------------------------------------------------------ #
    # Redis
    # ------------------------------------------------------------------ #
    redis_url: RedisDsn = Field(
        ...,
        description="Redis DSN (redis://host:port/db)",
    )

    # ------------------------------------------------------------------ #
    # Stripe — restricted keys only, never full secret keys
    # ------------------------------------------------------------------ #
    stripe_restricted_key: str = Field(
        ...,
        description=(
            "Stripe restricted API key scoped to PaymentIntents:write "
            "and Balance:read only. Never use a full secret key."
        ),
    )
    stripe_webhook_secret: str = Field(
        ...,
        description="Stripe webhook signing secret (whsec_...).",
    )

    # ------------------------------------------------------------------ #
    # Security
    # ------------------------------------------------------------------ #
    secret_key: str = Field(
        ...,
        description="64-char hex secret used for AES-256-GCM key encryption.",
        min_length=32,
    )

    # ------------------------------------------------------------------ #
    # Probe defaults
    # ------------------------------------------------------------------ #
    probe_mode: ProbeMode = Field(
        default=ProbeMode.PASSIVE_PRODUCTION,
        description=(
            "Controls whether synthetic PaymentIntent probes may run. "
            "Default passive_production disables all synthetic probe execution."
        ),
    )
    default_probe_interval_seconds: int = Field(default=900, ge=60)
    probe_timeout_seconds: int = Field(default=30, ge=5)

    # ------------------------------------------------------------------ #
    # Reconciliation defaults
    # ------------------------------------------------------------------ #
    recon_window_hours: int = Field(default=2, ge=1, le=24)
    recon_settlement_lag_minutes: int = Field(default=15, ge=1)

    @field_validator("stripe_restricted_key")
    @classmethod
    def validate_stripe_key_format(cls, v: str) -> str:
        """Reject full secret keys — only restricted keys are permitted."""
        if v.startswith("sk_"):
            raise ValueError(
                "Full Stripe secret keys (sk_...) are forbidden. "
                "Use a restricted key (rk_...) scoped to "
                "PaymentIntents:write and Balance:read only."
            )
        return v

    @property
    def is_production(self) -> bool:
        """Return True when running in production."""
        return self.environment == Environment.PRODUCTION

    @property
    def is_test(self) -> bool:
        """Return True when running in the test environment."""
        return self.environment == Environment.TEST


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the cached application settings singleton.

    Using lru_cache ensures settings are only parsed once per process.
    In tests, call get_settings.cache_clear() to reload from env.
    """
    return Settings()  # type: ignore[call-arg]
