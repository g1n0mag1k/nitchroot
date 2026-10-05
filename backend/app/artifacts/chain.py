"""Redis-backed per-merchant dossier hash chain for tamper-evidence."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
import structlog

log = structlog.get_logger(__name__)

_GENESIS = "GENESIS"
_KEY_PREFIX = "snitchroot:chain:"


@dataclass(frozen=True)
class ChainEntry:
    """One link in a merchant's dossier snapshot chain."""

    seq: int
    dossier_hash: str
    prev_hash: str
    timestamp: str
    discrepancy_id: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> ChainEntry:
        data: dict[str, Any] = json.loads(raw)
        return cls(
            seq=int(data["seq"]),
            dossier_hash=str(data["dossier_hash"]),
            prev_hash=str(data["prev_hash"]),
            timestamp=str(data["timestamp"]),
            discrepancy_id=str(data["discrepancy_id"]),
        )


class SnapshotChain:
    """Linked list of dossier SHA-256 hashes persisted in Redis."""

    def __init__(self, redis_client: aioredis.Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis_client

    @staticmethod
    def _key(merchant_id: uuid.UUID | str) -> str:
        return f"{_KEY_PREFIX}{merchant_id}"

    async def _load_entries(self, merchant_id: uuid.UUID | str) -> list[ChainEntry]:
        key = self._key(merchant_id)
        raw_items = await self._redis.lrange(key, 0, -1)
        return [ChainEntry.from_json(item) for item in raw_items]

    async def append(
        self,
        merchant_id: uuid.UUID | str,
        dossier_hash: str,
        discrepancy_id: uuid.UUID | str,
    ) -> ChainEntry:
        """Append a new chain link after the latest entry for this merchant."""
        entries = await self._load_entries(merchant_id)
        prev_hash = entries[-1].dossier_hash if entries else _GENESIS
        seq = len(entries)
        entry = ChainEntry(
            seq=seq,
            dossier_hash=dossier_hash,
            prev_hash=prev_hash,
            timestamp=datetime.now(UTC).isoformat(),
            discrepancy_id=str(discrepancy_id),
        )
        key = self._key(merchant_id)
        await self._redis.rpush(key, entry.to_json())
        log.info(
            "chain_appended",
            merchant_id=str(merchant_id),
            seq=seq,
            dossier_hash=dossier_hash,
            discrepancy_id=str(discrepancy_id),
        )
        return entry

    async def verify(self, merchant_id: uuid.UUID | str) -> tuple[bool, str]:
        """Walk the chain and confirm each prev_hash links to the prior dossier hash."""
        entries = await self._load_entries(merchant_id)
        if not entries:
            return True, "chain_ok"

        for index, entry in enumerate(entries):
            expected_prev = _GENESIS if index == 0 else entries[index - 1].dossier_hash
            if entry.prev_hash != expected_prev:
                return (
                    False,
                    f"broken at seq {entry.seq}: expected {expected_prev}, got {entry.prev_hash}",
                )
        return True, "chain_ok"

    async def export(self, merchant_id: uuid.UUID | str) -> list[ChainEntry]:
        """Return all chain entries in append order."""
        return await self._load_entries(merchant_id)
