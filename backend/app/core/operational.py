"""Production safety boundaries for synthetic Stripe probing."""

from __future__ import annotations

from app.core.config import Settings


def is_passive_production_mode(settings: Settings) -> bool:
    """Return True when live synthetic PaymentIntent probes must not run."""
    return settings.is_production and not settings.synthetic_probe_enabled


def synthetic_probes_permitted(settings: Settings) -> bool:
    """Return True only when configuration explicitly allows live synthetic probes."""
    return settings.synthetic_probe_enabled
