"""Reconciliation discrepancy listing."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_merchant
from app.core.database import get_db
from app.models.merchant import Merchant
from app.models.recon_discrepancy import ReconDiscrepancy

router = APIRouter(prefix="/api/v1", tags=["discrepancies"])

_MerchantDep = Depends(get_current_merchant)
_DbDep = Depends(get_db)


class DiscrepancyItem(BaseModel):
    id: uuid.UUID
    severity: str
    description: str
    detected_at: datetime
    resolved_at: datetime | None


class DiscrepancyListResponse(BaseModel):
    items: list[DiscrepancyItem]


@router.get(
    "/discrepancies",
    response_model=DiscrepancyListResponse,
    summary="List reconciliation discrepancies for the authenticated merchant",
)
async def list_discrepancies(
    merchant: Merchant = _MerchantDep,
    db: AsyncSession = _DbDep,
) -> DiscrepancyListResponse:
    result = await db.execute(
        select(ReconDiscrepancy)
        .where(ReconDiscrepancy.merchant_id == merchant.id)
        .order_by(ReconDiscrepancy.detected_at.desc())
    )
    rows = result.scalars().all()
    return DiscrepancyListResponse(
        items=[
            DiscrepancyItem(
                id=row.id,
                severity=row.severity,
                description=row.description,
                detected_at=row.detected_at,
                resolved_at=row.resolved_at,
            )
            for row in rows
        ]
    )
