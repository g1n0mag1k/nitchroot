"""Shared slowapi limiter and rate-limit exception handling."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)


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
