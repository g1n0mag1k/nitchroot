"""
Operational entry point for synthetic Stripe probes.

Scheduled monitoring and other application paths must use
SyntheticProbeExecutor rather than calling StripeProber directly.
StripeProber remains available for explicit sandbox/live modes and unit tests.
"""

from collections.abc import Callable

from app.core.config import ProbeMode, Settings, get_settings
from app.probes.models import ProbeRequest, ProbeResult
from app.probes.stripe_prober import StripeProber


class SyntheticProbeDisabledError(RuntimeError):
    """Raised when the current PROBE_MODE forbids synthetic probe execution."""


def is_live_restricted_key(stripe_restricted_key: str) -> bool:
    """Return True when the restricted key targets live Stripe (rk_live_...)."""
    return stripe_restricted_key.startswith("rk_live_")


class SyntheticProbeExecutor:
    """
    Enforces PROBE_MODE before delegating to StripeProber.

    passive_production: synthetic probes are never executed.
    synthetic_sandbox: probes run only against test restricted keys.
    live_synthetic_review_required: probes may run against live keys when
        PROBE_MODE is explicitly configured to this value.
    """

    def __init__(
        self,
        prober: StripeProber | None = None,
        settings_factory: Callable[[], Settings] | None = None,
    ) -> None:
        self._prober = prober or StripeProber()
        self._settings_factory = settings_factory or get_settings

    async def execute(self, request: ProbeRequest) -> ProbeResult:
        """
        Run a synthetic probe when permitted by PROBE_MODE.

        Raises:
            SyntheticProbeDisabledError: When the active mode blocks execution.
        """
        settings = self._settings_factory()
        mode = settings.probe_mode

        if mode == ProbeMode.PASSIVE_PRODUCTION:
            raise SyntheticProbeDisabledError(
                "Synthetic PaymentIntent probes are disabled in "
                "passive_production mode (PROBE_MODE=passive_production)."
            )

        if mode == ProbeMode.SYNTHETIC_SANDBOX:
            if is_live_restricted_key(request.stripe_restricted_key):
                raise SyntheticProbeDisabledError(
                    "synthetic_sandbox mode does not permit live Stripe "
                    "restricted keys; use a test key (rk_test_...)."
                )
            return await self._prober.probe(request)

        if mode == ProbeMode.LIVE_SYNTHETIC_REVIEW_REQUIRED:
            return await self._prober.probe(request)

        raise SyntheticProbeDisabledError(f"Unsupported probe mode: {mode!s}")
