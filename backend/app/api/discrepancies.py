"""Open reconciliation discrepancy listing."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.recon_discrepancy import ReconDiscrepancy

router = APIRouter(prefix="/api/v1", tags=["discrepancies"])

_DbDep = Depends(get_db)
_MerchantIdQuery = Query(..., description="Merchant UUID")


class DiscrepancyResponse(BaseModel):
    """Serialized open reconciliation discrepancy."""

    id: uuid.UUID
    merchant_id: uuid.UUID
    webhook_event_id: uuid.UUID | None
    probe_result_id: uuid.UUID | None
    stripe_intent_id: str | None
    severity: str
    description: str
    detected_at: datetime
    resolved_at: datetime | None = None


class DiscrepancyListResponse(BaseModel):
    """List wrapper for discrepancy rows."""

    items: list[DiscrepancyResponse] = Field(default_factory=list)


@router.get(
    "/discrepancies",
    response_model=DiscrepancyListResponse,
    summary="List open reconciliation discrepancies",
)
async def list_open_discrepancies(
    merchant_id: uuid.UUID = _MerchantIdQuery,
    db: AsyncSession = _DbDep,
) -> DiscrepancyListResponse:
    """Return unresolved discrepancies for the given merchant."""
    stmt = (
        select(ReconDiscrepancy)
        .where(
            ReconDiscrepancy.merchant_id == merchant_id,
            ReconDiscrepancy.resolved_at.is_(None),
        )
        .order_by(ReconDiscrepancy.detected_at.desc())
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return DiscrepancyListResponse(
        items=[
            DiscrepancyResponse(
                id=row.id,
                merchant_id=row.merchant_id,
                webhook_event_id=row.webhook_event_id,
                probe_result_id=row.probe_result_id,
                stripe_intent_id=row.stripe_intent_id,
                severity=row.severity,
                description=row.description,
                detected_at=row.detected_at,
                resolved_at=row.resolved_at,
            )
            for row in rows
        ]
    )
