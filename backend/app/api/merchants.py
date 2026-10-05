"""Merchant registration and probe chain endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import generate_api_key, get_current_merchant
from app.core.config import Settings
from app.core.crypto import encrypt
from app.core.database import get_db
from app.models.api_key import APIKey
from app.models.merchant import Merchant
from app.models.probe_result import ProbeResultRecord

router = APIRouter(prefix="/api/v1/merchants", tags=["merchants"])

_DbDep = Depends(get_db)
_MerchantDep = Depends(get_current_merchant)


class MerchantCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    stripe_restricted_key: str
    probe_interval_seconds: int = Field(default=900, ge=60)

    @field_validator("stripe_restricted_key")
    @classmethod
    def validate_stripe_restricted_key(cls, v: str) -> str:
        Settings.validate_stripe_key_format(v)
        if not v.startswith("rk_"):
            raise ValueError("stripe_restricted_key must be a restricted key (rk_...)")
        return v


class MerchantCreateResponse(BaseModel):
    merchant_id: uuid.UUID
    api_key: str


class ProbeChainItem(BaseModel):
    id: uuid.UUID
    status: str
    stripe_intent_id: str | None
    latency_ms: int
    probed_at: datetime


class ProbeChainResponse(BaseModel):
    merchant_id: uuid.UUID
    results: list[ProbeChainItem]


@router.post(
    "",
    response_model=MerchantCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a merchant (bootstrap)",
)
async def register_merchant(
    body: MerchantCreateRequest,
    db: AsyncSession = _DbDep,
) -> MerchantCreateResponse:
    """Create a merchant, encrypt the Stripe key, and issue a one-time API key."""
    merchant = Merchant(
        name=body.name,
        stripe_restricted_key_enc=encrypt(body.stripe_restricted_key),
        probe_interval_seconds=body.probe_interval_seconds,
    )
    db.add(merchant)
    await db.flush()

    full_key, key_hash = generate_api_key()
    api_key = APIKey(
        merchant_id=merchant.id,
        key_prefix=full_key[:8],
        key_hash=key_hash,
        scopes=APIKey.scopes_to_storage(["read", "write"]),
    )
    db.add(api_key)
    await db.flush()

    return MerchantCreateResponse(merchant_id=merchant.id, api_key=full_key)


@router.get(
    "/{merchant_id}/chain",
    response_model=ProbeChainResponse,
    summary="Probe result chain for a merchant",
)
async def get_merchant_chain(
    merchant_id: uuid.UUID,
    merchant: Merchant = _MerchantDep,
    db: AsyncSession = _DbDep,
) -> ProbeChainResponse:
    if merchant_id != merchant.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    result = await db.execute(
        select(ProbeResultRecord)
        .where(ProbeResultRecord.merchant_id == merchant_id)
        .order_by(ProbeResultRecord.probed_at.desc())
    )
    rows = result.scalars().all()
    return ProbeChainResponse(
        merchant_id=merchant_id,
        results=[
            ProbeChainItem(
                id=row.id,
                status=row.status,
                stripe_intent_id=row.stripe_intent_id,
                latency_ms=row.latency_ms,
                probed_at=row.probed_at,
            )
            for row in rows
        ],
    )
