"""Tests for API key auth and merchant registration."""

from __future__ import annotations

import uuid

import pytest
from app.auth import generate_api_key
from app.models.api_key import APIKey
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_merchant_creation_returns_sr_api_key(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/merchants",
        json={
            "name": "Acme",
            "stripe_restricted_key": "rk_test_valid_key",
            "probe_interval_seconds": 900,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["api_key"].startswith("sr_")
    assert len(body["api_key"]) == 40
    uuid.UUID(body["merchant_id"])


@pytest.mark.asyncio
async def test_api_key_hash_stored_not_plaintext(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    response = await client.post(
        "/api/v1/merchants",
        json={"name": "Hash Co", "stripe_restricted_key": "rk_test_hash_check"},
    )
    assert response.status_code == 201
    api_key = response.json()["api_key"]

    result = await db_session.execute(select(APIKey))
    row = result.scalar_one()
    assert row.key_hash != api_key
    assert len(row.key_hash) == 64

    # Full API key must not appear anywhere in api_keys table text columns
    table_dump = await db_session.execute(text("SELECT key_prefix, key_hash, scopes FROM api_keys"))
    for prefix, key_hash, scopes in table_dump.all():
        assert api_key not in (prefix, key_hash, scopes or "")


@pytest.mark.asyncio
async def test_protected_endpoints_require_bearer(client: AsyncClient) -> None:
    for path in ("/api/v1/discrepancies", f"/api/v1/merchants/{uuid.uuid4()}/chain"):
        response = await client.get(path)
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_invalid_api_key_returns_401(client: AsyncClient) -> None:
    headers = {"Authorization": "Bearer sr_invalid_key_xxxxxxxxxxxxxxxxxxxxxxxxx"}
    response = await client.get("/api/v1/discrepancies", headers=headers)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_valid_api_key_returns_200(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    response = await client.get("/api/v1/discrepancies", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {"items": []}


@pytest.mark.asyncio
async def test_chain_endpoint_with_valid_key(
    client: AsyncClient,
    auth_headers: dict[str, str],
    registered_merchant_id: uuid.UUID,
) -> None:
    response = await client.get(
        f"/api/v1/merchants/{registered_merchant_id}/chain",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["merchant_id"] == str(registered_merchant_id)
    assert body["results"] == []


def test_generate_api_key_format() -> None:
    full_key, key_hash = generate_api_key()
    assert full_key.startswith("sr_")
    assert len(full_key) == 40
    assert len(key_hash) == 64
