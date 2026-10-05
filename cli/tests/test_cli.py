"""CLI integration tests with respx (no live HTTP)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from conftest import write_config
from typer.testing import CliRunner

from snitchroot_cli.main import app

runner = CliRunner()


@respx.mock
def test_login_saves_config(config_file: Path, api_base: str) -> None:
    respx.get(f"{api_base}/health").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "version": "0.1.0",
                "environment": "test",
                "merchant_id": "merch-abc",
            },
        )
    )

    result = runner.invoke(app, ["login", "sr_test_key", "--base-url", api_base])
    assert result.exit_code == 0
    assert config_file.is_file()
    data = json.loads(config_file.read_text())
    assert data["api_key"] == "sr_test_key"
    assert data["merchant_id"] == "merch-abc"
    assert data["base_url"] == api_base


@respx.mock
def test_login_auth_failure_exits_2(config_file: Path, api_base: str) -> None:
    respx.get(f"{api_base}/health").mock(return_value=httpx.Response(401))

    result = runner.invoke(app, ["login", "sr_bad", "--base-url", api_base])
    assert result.exit_code == 2
    assert not config_file.exists()


@respx.mock
def test_login_network_failure_exits_2(config_file: Path, api_base: str) -> None:
    respx.get(f"{api_base}/health").mock(side_effect=httpx.ConnectError("refused"))

    result = runner.invoke(app, ["login", "sr_test", "--base-url", api_base])
    assert result.exit_code == 2


@respx.mock
def test_discrepancies_table_ok(
    config_file: Path,
    api_base: str,
) -> None:
    write_config(config_file, "sr_ok", "merch-1", api_base)
    respx.get(f"{api_base}/api/v1/discrepancies").mock(
        return_value=httpx.Response(
            200,
            json={
                "discrepancies": [
                    {
                        "id": "d1",
                        "severity": "LOW",
                        "status": "open",
                        "description": "minor",
                    }
                ]
            },
        )
    )

    result = runner.invoke(app, ["discrepancies"])
    assert result.exit_code == 0
    assert "d1" in result.output


@respx.mock
def test_discrepancies_high_open_exits_1(
    config_file: Path,
    api_base: str,
) -> None:
    write_config(config_file, "sr_ok", "merch-1", api_base)
    respx.get(f"{api_base}/api/v1/discrepancies").mock(
        return_value=httpx.Response(
            200,
            json={
                "discrepancies": [
                    {
                        "id": "d-high",
                        "severity": "HIGH",
                        "status": "open",
                        "description": "settlement missing",
                    }
                ]
            },
        )
    )

    result = runner.invoke(app, ["discrepancies"])
    assert result.exit_code == 1


@respx.mock
def test_discrepancies_json(
    config_file: Path,
    api_base: str,
) -> None:
    write_config(config_file, "sr_ok", "merch-1", api_base)
    respx.get(f"{api_base}/api/v1/discrepancies").mock(
        return_value=httpx.Response(200, json={"discrepancies": []})
    )

    result = runner.invoke(app, ["discrepancies", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


@respx.mock
def test_chain_verify_only_success(
    config_file: Path,
    api_base: str,
) -> None:
    write_config(config_file, "sr_ok", "merch-1", api_base)
    respx.get(f"{api_base}/api/v1/merchants/merch-1/chain").mock(
        return_value=httpx.Response(
            200,
            json={"verified": True, "entries": [{"index": 0, "hash": "abc"}]},
        )
    )

    result = runner.invoke(app, ["chain", "--verify-only"])
    assert result.exit_code == 0
    assert result.stdout.strip() == ""


@respx.mock
def test_chain_unverified_exits_1(
    config_file: Path,
    api_base: str,
) -> None:
    write_config(config_file, "sr_ok", "merch-1", api_base)
    respx.get(f"{api_base}/api/v1/merchants/merch-1/chain").mock(
        return_value=httpx.Response(
            200,
            json={"verified": False, "entries": []},
        )
    )

    result = runner.invoke(app, ["chain", "--verify-only"])
    assert result.exit_code == 1


@respx.mock
def test_chain_json_output(
    config_file: Path,
    api_base: str,
) -> None:
    write_config(config_file, "sr_ok", "merch-1", api_base)
    respx.get(f"{api_base}/api/v1/merchants/merch-1/chain").mock(
        return_value=httpx.Response(
            200,
            json={
                "verified": True,
                "entries": [
                    {
                        "index": 0,
                        "hash": "deadbeef",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "event_type": "dossier",
                    }
                ],
            },
        )
    )

    result = runner.invoke(app, ["chain", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["verified"] is True
    assert payload["entries"][0]["hash"] == "deadbeef"


@respx.mock
def test_register_saves_api_key(
    config_file: Path,
    api_base: str,
) -> None:
    route = respx.post(f"{api_base}/api/v1/merchants").mock(
        return_value=httpx.Response(
            201,
            json={
                "merchant_id": "merch-new",
                "api_key": "sr_one_time",
                "name": "Acme",
            },
        )
    )

    result = runner.invoke(
        app,
        [
            "register",
            "--name",
            "Acme",
            "--key",
            "rk_test_123",
            "--interval",
            "900",
            "--base-url",
            api_base,
        ],
    )
    assert result.exit_code == 0
    assert route.called
    request = route.calls[0].request
    body = json.loads(request.content.decode())
    assert body["name"] == "Acme"
    assert body["stripe_restricted_key"] == "rk_test_123"
    assert body["probe_interval_seconds"] == 900

    data = json.loads(config_file.read_text())
    assert data["api_key"] == "sr_one_time"
    assert data["merchant_id"] == "merch-new"
    assert "will not be shown again" in result.stderr.lower() or "Warning" in result.stderr


def test_discrepancies_without_login_exits_2() -> None:
    result = runner.invoke(app, ["discrepancies"])
    assert result.exit_code == 2
