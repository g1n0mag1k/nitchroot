"""HTTP security headers applied to every API response."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import Environment, get_settings

_BASE_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": (
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
        "magnetometer=(), microphone=(), payment=(), usb=()"
    ),
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}

_HSTS_HEADER = "Strict-Transport-Security"
_HSTS_VALUE = "max-age=31536000; includeSubDomains"
_CACHE_CONTROL = "Cache-Control"
_NO_STORE = "no-store"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach standard security headers to all outbound responses."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        settings = get_settings()
        for name, value in _BASE_SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if settings.environment == Environment.PRODUCTION:
            response.headers.setdefault(_HSTS_HEADER, _HSTS_VALUE)
        if request.url.path.startswith(("/api/", "/webhooks/", "/health")):
            response.headers[_CACHE_CONTROL] = _NO_STORE
        return response
