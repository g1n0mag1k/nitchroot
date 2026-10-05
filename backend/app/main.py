"""
SnitchRoot FastAPI application factory.

Creates and configures the FastAPI application instance with:
  - Structured logging initialised before first request
  - API routers mounted under /api/v1
  - Global exception handlers for clean JSON error responses
  - Lifespan context manager for startup/shutdown hooks
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan context manager.

    Runs startup logic before yielding and teardown logic after.
    Add database pool warm-up, Redis connection checks, and scheduler
    start here in subsequent phases.
    """
    settings = get_settings()
    logger.info(
        "snitchroot_starting",
        version=settings.app_version,
        environment=settings.environment.value,
    )
    yield
    logger.info("snitchroot_shutdown")


def create_app() -> FastAPI:
    """
    Construct and configure the FastAPI application instance.

    Returns:
        A fully configured FastAPI application.
    """
    configure_logging()
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Zero-friction payment rail sentry and automated "
            "ledger reconciliation sentinel."
        ),
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ------------------------------------------------------------------ #
    # Routers
    # ------------------------------------------------------------------ #
    app.include_router(health_router)

    # ------------------------------------------------------------------ #
    # Global exception handlers
    # ------------------------------------------------------------------ #
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        """
        Catch-all handler that returns a structured JSON error response
        instead of leaking stack traces to clients.
        """
        log = structlog.get_logger()
        log.exception(
            "unhandled_exception",
            method=request.method,
            url=str(request.url),
            exc_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "detail": "An unexpected error occurred.",
            },
        )

    return app
