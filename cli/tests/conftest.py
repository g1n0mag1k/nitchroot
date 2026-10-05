"""Shared fixtures for CLI tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CLI at a temporary config path."""
    path = tmp_path / "config.json"
    monkeypatch.setenv("SNITCHROOT_CONFIG", str(path))
    return path


@pytest.fixture
def api_base(monkeypatch: pytest.MonkeyPatch) -> str:
    """Default mocked API base URL."""
    base = "https://api.snitchroot.test"
    monkeypatch.setenv("SNITCHROOT_API_URL", base)
    return base


def write_config(path: Path, api_key: str, merchant_id: str, base_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "api_key": api_key,
                "merchant_id": merchant_id,
                "base_url": base_url,
            }
        ),
        encoding="utf-8",
    )
