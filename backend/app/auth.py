"""API key generation and Bearer authentication."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.api_key import APIKey
from app.models.merchant import Merchant

_DbDep = Depends(get_db)

_KEY_PREFIX = "sr_"
_FULL_KEY_LENGTH = 40


def _hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str]:
    """
    Generate a new API key.

    Returns:
        Tuple of (full_key shown once to the caller, SHA-256 hex hash for storage).
    """
    random_part_len = _FULL_KEY_LENGTH - len(_KEY_PREFIX)
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    random_part = "".join(secrets.choice(alphabet) for _ in range(random_part_len))
    full_key = f"{_KEY_PREFIX}{random_part}"
    return full_key, _hash_api_key(full_key)


async def verify_api_key(raw_key: str, db: AsyncSession) -> APIKey | None:
    """Look up an active API key by hash and refresh ``last_used_at``."""
    key_hash = _hash_api_key(raw_key)
    result = await db.execute(
        select(APIKey).where(APIKey.key_hash == key_hash, APIKey.is_active.is_(True))
    )
    api_key = result.scalar_one_or_none()
    if api_key is None:
        return None

    api_key.last_used_at = datetime.now(UTC)
    await db.flush()
    return api_key


async def get_current_merchant(
    authorization: str | None = Header(None, alias="Authorization"),
    db: AsyncSession = _DbDep,
) -> Merchant:
    """FastAPI dependency: resolve Bearer API key to the owning merchant."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )

    raw_key = authorization.removeprefix("Bearer ").strip()
    if not raw_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
        )

    api_key = await verify_api_key(raw_key, db)
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    result = await db.execute(select(Merchant).where(Merchant.id == api_key.merchant_id))
    merchant = result.scalar_one_or_none()
    if merchant is None or not merchant.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return merchant
