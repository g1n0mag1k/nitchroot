"""Merchant registration API (in-memory stub until ORM persistence ships)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field, field_validator

from app.core.rate_limit import limiter

router = APIRouter(prefix="/api/v1", tags=["merchants"])


class MerchantCreateRequest(BaseModel):
    """Payload for registering a new merchant probe target."""

    name: str = Field(..., min_length=1, max_length=200)
    stripe_restricted_key: str = Field(
        ...,
        description="Stripe restricted key (rk_...) scoped to probe operations.",
    )
    probe_interval_seconds: int = Field(default=900, ge=60, le=86400)

    @field_validator("stripe_restricted_key")
    @classmethod
    def reject_secret_keys(cls, value: str) -> str:
        if value.startswith("sk_"):
            raise ValueError(
                "Full Stripe secret keys (sk_...) are forbidden. Use a restricted key (rk_...)."
            )
        return value


class MerchantCreateResponse(BaseModel):
    """Response returned after merchant registration."""

    id: uuid.UUID
    name: str
    probe_interval_seconds: int
    created_at: datetime


@router.post(
    "/merchants",
    status_code=status.HTTP_201_CREATED,
    response_model=MerchantCreateResponse,
    summary="Register a merchant",
    description=(
        "Registers a merchant for zero-fee Stripe probing. "
        "Persists encrypted credentials once database models are wired."
    ),
)
@limiter.limit("10/hour")
async def register_merchant(
    request: Request,
    body: MerchantCreateRequest,
) -> MerchantCreateResponse:
    """Accept a merchant registration request (stub — returns a new UUID)."""
    return MerchantCreateResponse(
        id=uuid.uuid4(),
        name=body.name,
        probe_interval_seconds=body.probe_interval_seconds,
        created_at=datetime.now(UTC),
    )
