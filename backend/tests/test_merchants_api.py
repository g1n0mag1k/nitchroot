"""Tests for merchant chain export API."""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import AsyncClient

from tests.test_chain import FakeRedis


@pytest.fixture
def chain_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
async def chain_client(chain_redis: FakeRedis) -> AsyncClient:
    from app.core.redis import get_redis
    from app.main import create_app
    from httpx import ASGITransport

    app = create_app()
    app.dependency_overrides[get_redis] = lambda: chain_redis
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()


class TestMerchantChainEndpoint:
    async def test_returns_chain_with_verified_flag(
        self, chain_client: AsyncClient, chain_redis: FakeRedis
    ) -> None:
        merchant_id = uuid.uuid4()
        key = f"snitchroot:chain:{merchant_id}"
        entry = {
            "seq": 0,
            "dossier_hash": "abc123",
            "prev_hash": "GENESIS",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "discrepancy_id": str(uuid.uuid4()),
        }
        await chain_redis.rpush(key, json.dumps(entry))

        response = await chain_client.get(f"/api/v1/merchants/{merchant_id}/chain")
        assert response.status_code == 200
        body = response.json()
        assert body["verified"] is True
        assert body["verification_message"] == "chain_ok"
        assert len(body["entries"]) == 1
        assert body["entries"][0]["dossier_hash"] == "abc123"

    async def test_corrupt_chain_reports_unverified(
        self, chain_client: AsyncClient, chain_redis: FakeRedis
    ) -> None:
        merchant_id = uuid.uuid4()
        key = f"snitchroot:chain:{merchant_id}"
        first = {
            "seq": 0,
            "dossier_hash": "h1",
            "prev_hash": "GENESIS",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "discrepancy_id": str(uuid.uuid4()),
        }
        second = {
            "seq": 1,
            "dossier_hash": "h2",
            "prev_hash": "broken",
            "timestamp": "2024-01-01T00:00:01+00:00",
            "discrepancy_id": str(uuid.uuid4()),
        }
        await chain_redis.rpush(key, json.dumps(first))
        await chain_redis.rpush(key, json.dumps(second))

        response = await chain_client.get(f"/api/v1/merchants/{merchant_id}/chain")
        body = response.json()
        assert body["verified"] is False
        assert "broken at seq 1" in body["verification_message"]
