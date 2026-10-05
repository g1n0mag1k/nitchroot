"""
Tests for GET /health endpoint.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_200(client: AsyncClient) -> None:
    """Health endpoint must return HTTP 200."""
    response = await client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_response_schema(client: AsyncClient) -> None:
    """Health response must contain status, version, and environment."""
    response = await client.get("/health")
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
    assert body["environment"] == "test"


@pytest.mark.asyncio
async def test_health_is_unauthenticated(client: AsyncClient) -> None:
    """Health endpoint must be reachable without any auth headers."""
    response = await client.get("/health", headers={})
    assert response.status_code == 200
