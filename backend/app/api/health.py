"""
Health check endpoint.

GET /health — returns application status, version, and environment.
Used by load balancers, Docker healthchecks, and uptime monitors.
Does not require authentication.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings

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
