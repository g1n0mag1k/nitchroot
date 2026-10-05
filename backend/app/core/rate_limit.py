"""Shared slowapi limiter (Redis-backed) and rate-limit exception handling."""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from typing import cast

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request as StarletteRequest

from app.core.config import get_settings


def _parse_trusted_proxy_networks(
    cidrs: tuple[str, ...],
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for cidr in cidrs:
        if not cidr.strip():
            continue
        networks.append(ipaddress.ip_network(cidr.strip(), strict=False))
    return tuple(networks)


def _peer_is_trusted_proxy(
    peer_host: str,
    networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
) -> bool:
    if not networks:
        return False
    try:
        peer = ipaddress.ip_address(peer_host)
    except ValueError:
        return False
    return any(peer in network for network in networks)


def rate_limit_client_key(request: StarletteRequest) -> str:
    """
    Rate-limit key derived from the client IP.

    Honors X-Forwarded-For only when the immediate peer is a configured
    trusted reverse proxy (CIDR list from settings).
    """
    settings = get_settings()
    peer = request.client.host if request.client else "unknown"
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        networks = _parse_trusted_proxy_networks(settings.trusted_proxy_cidr_list)
        if _peer_is_trusted_proxy(peer, networks):
            client_ip = forwarded_for.split(",")[0].strip()
            if client_ip:
                return client_ip
    return peer


def stripe_webhook_global_key(
    _request: StarletteRequest | None = None,
) -> str:
    """Fixed key for the Stripe webhook global circuit breaker."""
    return "stripe_webhook_global"


@lru_cache(maxsize=1)
def get_limiter() -> Limiter:
    """Return a process-wide limiter using Redis storage (no in-memory fallback)."""
    settings = get_settings()
    return Limiter(
        key_func=rate_limit_client_key,
        storage_uri=str(settings.redis_url),
        in_memory_fallback_enabled=False,
    )


class _LazyLimiter:
    """Defer limiter construction until first use (after settings/env are ready)."""

    def __getattr__(self, name: str) -> object:
        return getattr(get_limiter(), name)


# Module-level alias used by route decorators at import time.
limiter: Limiter = cast(Limiter, _LazyLimiter())


async def rate_limit_exceeded_handler(
    request: Request,
    exc: RateLimitExceeded,
) -> JSONResponse:
    """Return HTTP 429 with a Retry-After header for throttled clients."""
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is None:
        retry_after = 60

    response = JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "detail": str(exc.detail),
        },
    )
    response.headers["Retry-After"] = str(int(retry_after))
    return response
