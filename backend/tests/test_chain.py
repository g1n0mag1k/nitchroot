"""Tests for the Redis-backed dossier snapshot chain."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from app.artifacts.chain import SnapshotChain


class FakeRedis:
    """Minimal async Redis list stand-in for chain unit tests."""

    def __init__(self) -> None:
        self._lists: dict[str, list[str]] = {}

    async def rpush(self, key: str, value: str) -> int:
        self._lists.setdefault(key, []).append(value)
        return len(self._lists[key])

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self._lists.get(key, [])
        if not items:
            return []
        if end == -1:
            end = len(items) - 1
        return items[start : end + 1]


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def chain(fake_redis: FakeRedis) -> SnapshotChain:
    return SnapshotChain(fake_redis)


class TestSnapshotChain:
    async def test_genesis_entry_prev_hash(self, chain: SnapshotChain) -> None:
        merchant_id = uuid.uuid4()
        entry = await chain.append(merchant_id, "hash_a", uuid.uuid4())
        assert entry.seq == 0
        assert entry.prev_hash == "GENESIS"

    async def test_two_entries_link_prev_hash(self, chain: SnapshotChain) -> None:
        merchant_id = uuid.uuid4()
        first = await chain.append(merchant_id, "hash_first", uuid.uuid4())
        second = await chain.append(merchant_id, "hash_second", uuid.uuid4())
        assert first.prev_hash == "GENESIS"
        assert second.prev_hash == "hash_first"
        assert second.seq == 1

    async def test_export_matches_append_order(self, chain: SnapshotChain) -> None:
        merchant_id = uuid.uuid4()
        await chain.append(merchant_id, "h1", uuid.uuid4())
        await chain.append(merchant_id, "h2", uuid.uuid4())
        await chain.append(merchant_id, "h3", uuid.uuid4())
        exported = await chain.export(merchant_id)
        assert [e.dossier_hash for e in exported] == ["h1", "h2", "h3"]
        assert [e.seq for e in exported] == [0, 1, 2]

    async def test_verify_ok_on_valid_chain(self, chain: SnapshotChain) -> None:
        merchant_id = uuid.uuid4()
        await chain.append(merchant_id, "h1", uuid.uuid4())
        await chain.append(merchant_id, "h2", uuid.uuid4())
        ok, message = await chain.verify(merchant_id)
        assert ok is True
        assert message == "chain_ok"

    async def test_verify_fails_after_redis_corruption(
        self, chain: SnapshotChain, fake_redis: FakeRedis
    ) -> None:
        merchant_id = uuid.uuid4()
        await chain.append(merchant_id, "h1", uuid.uuid4())
        await chain.append(merchant_id, "h2", uuid.uuid4())
        key = f"snitchroot:chain:{merchant_id}"
        corrupted: dict[str, Any] = json.loads(fake_redis._lists[key][1])
        corrupted["prev_hash"] = "tampered"
        fake_redis._lists[key][1] = json.dumps(corrupted)
        ok, message = await chain.verify(merchant_id)
        assert ok is False
        assert message == "broken at seq 1: expected h1, got tampered"

    async def test_empty_chain_verifies(self, chain: SnapshotChain) -> None:
        ok, message = await chain.verify(uuid.uuid4())
        assert ok is True
        assert message == "chain_ok"
