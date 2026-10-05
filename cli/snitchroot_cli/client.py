"""HTTP client for the SnitchRoot API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class ApiError(Exception):
    """Base class for API client errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthError(ApiError):
    """Authentication or authorization failure."""


class NetworkError(ApiError):
    """Connection, timeout, or transport failure."""


@dataclass(frozen=True)
class HealthResult:
    """Authenticated health check payload."""

    status: str
    merchant_id: str
    version: str | None = None
    environment: str | None = None


@dataclass(frozen=True)
class MerchantRegistration:
    """Response from merchant registration."""

    merchant_id: str
    api_key: str
    name: str


@dataclass(frozen=True)
class Discrepancy:
    """Single reconciliation discrepancy."""

    id: str
    severity: str
    status: str
    description: str


@dataclass(frozen=True)
class ChainEntry:
    """Immutable evidence chain link."""

    index: int
    hash: str
    timestamp: str
    event_type: str


@dataclass(frozen=True)
class ChainResult:
    """Merchant evidence chain verification result."""

    verified: bool
    entries: list[ChainEntry]


class SnitchRootClient:
    """Thin httpx wrapper for SnitchRoot REST endpoints."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout

    def _headers(self, *, auth: bool) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if auth and self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        auth: bool = True,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        url = f"{self._base_url}{path}"
        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.request(
                    method,
                    url,
                    headers=self._headers(auth=auth),
                    json=json_body,
                )
        except httpx.RequestError as exc:
            raise NetworkError(str(exc)) from exc

        if response.status_code in (401, 403):
            raise AuthError(
                f"Authentication failed ({response.status_code})",
                status_code=response.status_code,
            )
        return response

    def validate_login(self, api_key: str) -> HealthResult:
        """
        Validate an API key via GET /health with Bearer auth.

        The API returns merchant_id in the health payload when the key is valid.
        """
        client = SnitchRootClient(self._base_url, api_key=api_key, timeout=self._timeout)
        response = client._request("GET", "/health", auth=True)
        if response.status_code >= 500:
            raise NetworkError(
                f"Server error ({response.status_code})",
                status_code=response.status_code,
            )
        if response.status_code != 200:
            raise AuthError(
                f"Invalid API key or health check failed ({response.status_code})",
                status_code=response.status_code,
            )
        data = response.json()
        merchant_id = data.get("merchant_id")
        if not merchant_id:
            raise ApiError("Health response missing merchant_id")
        return HealthResult(
            status=str(data.get("status", "ok")),
            merchant_id=str(merchant_id),
            version=data.get("version"),
            environment=data.get("environment"),
        )

    def register_merchant(
        self,
        name: str,
        stripe_restricted_key: str,
        probe_interval_seconds: int,
    ) -> MerchantRegistration:
        """POST /api/v1/merchants — create a merchant and receive a one-time API key."""
        body = {
            "name": name,
            "stripe_restricted_key": stripe_restricted_key,
            "probe_interval_seconds": probe_interval_seconds,
        }
        response = self._request(
            "POST",
            "/api/v1/merchants",
            auth=False,
            json_body=body,
        )
        if response.status_code >= 500:
            raise NetworkError(
                f"Server error ({response.status_code})",
                status_code=response.status_code,
            )
        if response.status_code not in (200, 201):
            detail = response.text
            raise ApiError(
                f"Registration failed ({response.status_code}): {detail}",
                status_code=response.status_code,
            )
        data = response.json()
        merchant_id = data.get("merchant_id") or data.get("id")
        api_key = data.get("api_key")
        if not merchant_id or not api_key:
            raise ApiError("Registration response missing merchant_id or api_key")
        return MerchantRegistration(
            merchant_id=str(merchant_id),
            api_key=str(api_key),
            name=str(data.get("name", name)),
        )

    def list_discrepancies(self) -> list[Discrepancy]:
        """GET /api/v1/discrepancies."""
        response = self._request("GET", "/api/v1/discrepancies", auth=True)
        if response.status_code >= 500:
            raise NetworkError(
                f"Server error ({response.status_code})",
                status_code=response.status_code,
            )
        if response.status_code != 200:
            raise ApiError(
                f"Failed to list discrepancies ({response.status_code})",
                status_code=response.status_code,
            )
        data = response.json()
        raw_items = data if isinstance(data, list) else data.get("discrepancies", [])
        items: list[Discrepancy] = []
        for item in raw_items:
            items.append(
                Discrepancy(
                    id=str(item.get("id", "")),
                    severity=str(item.get("severity", "")).upper(),
                    status=str(item.get("status", "open")).lower(),
                    description=str(item.get("description", "")),
                )
            )
        return items

    def get_chain(self, merchant_id: str) -> ChainResult:
        """GET /api/v1/merchants/{merchant_id}/chain."""
        response = self._request(
            "GET",
            f"/api/v1/merchants/{merchant_id}/chain",
            auth=True,
        )
        if response.status_code >= 500:
            raise NetworkError(
                f"Server error ({response.status_code})",
                status_code=response.status_code,
            )
        if response.status_code != 200:
            raise ApiError(
                f"Failed to fetch chain ({response.status_code})",
                status_code=response.status_code,
            )
        data = response.json()
        entries: list[ChainEntry] = []
        for item in data.get("entries", []):
            entries.append(
                ChainEntry(
                    index=int(item.get("index", len(entries))),
                    hash=str(item.get("hash", "")),
                    timestamp=str(item.get("timestamp", "")),
                    event_type=str(item.get("event_type", "")),
                )
            )
        verified = bool(data.get("verified", False))
        return ChainResult(verified=verified, entries=entries)
