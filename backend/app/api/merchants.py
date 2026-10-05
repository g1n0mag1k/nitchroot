"""Merchant-scoped API routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from app.artifacts.chain import ChainEntry, SnapshotChain
from app.core.redis import get_redis

router = APIRouter(prefix="/api/v1/merchants", tags=["merchants"])

_RedisDep = Depends(get_redis)


class ChainEntryResponse(BaseModel):
    seq: int
    dossier_hash: str
    prev_hash: str
    timestamp: str
    discrepancy_id: str


class MerchantChainResponse(BaseModel):
    merchant_id: uuid.UUID
    verified: bool
    verification_message: str
    entries: list[ChainEntryResponse] = Field(default_factory=list)


def _entry_to_response(entry: ChainEntry) -> ChainEntryResponse:
    return ChainEntryResponse(
        seq=entry.seq,
        dossier_hash=entry.dossier_hash,
        prev_hash=entry.prev_hash,
        timestamp=entry.timestamp,
        discrepancy_id=entry.discrepancy_id,
    )


@router.get(
    "/{merchant_id}/chain",
    response_model=MerchantChainResponse,
    summary="Export dossier snapshot chain",
    description=(
        "Returns the full per-merchant dossier hash chain from Redis and whether "
        "the chain links verify locally."
    ),
)
async def get_merchant_chain(
    merchant_id: uuid.UUID,
    redis: Redis = _RedisDep,  # type: ignore[type-arg]
) -> MerchantChainResponse:
    chain = SnapshotChain(redis)
    entries = await chain.export(merchant_id)
    verified, message = await chain.verify(merchant_id)
    return MerchantChainResponse(
        merchant_id=merchant_id,
        verified=verified,
        verification_message=message,
        entries=[_entry_to_response(entry) for entry in entries],
    )
