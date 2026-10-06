"""
Health check endpoint.

GET /health — returns application status, version, and environment.
Used by load balancers, Docker healthchecks, and uptime monitors.
Does not require authentication.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.redis import get_redis

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Response schema for the health check endpoint."""

    status: str
    version: str
    environment: str


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description=(
        "Returns application health status. "
        "A 200 response confirms the API process is running. "
        "Does not verify database or Redis connectivity."
    ),
)
async def health_check() -> HealthResponse:
    """
    Return application health status.

    Returns:
        HealthResponse with status 'ok', current version, and environment.
    """
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        environment=settings.environment.value,
    )


@router.get("/health/ready")
async def health_ready(
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> dict[str, str]:
    """Readiness probe: verify database and Redis connectivity."""
    database_status = "ok"
    redis_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        database_status = "error"
    try:
        await redis.ping()
    except Exception:
        redis_status = "error"

    if database_status == "ok" and redis_status == "ok":
        return {"status": "ready", "database": "ok", "redis": "ok"}

    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=503,
        content={
            "status": "unavailable",
            "database": database_status,
            "redis": redis_status,
        },
    )
