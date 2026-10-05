"""SnitchRoot Typer CLI entrypoint."""

from __future__ import annotations

import json
import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from snitchroot_cli.client import (
    ApiError,
    AuthError,
    NetworkError,
    SnitchRootClient,
)
from snitchroot_cli.config import (
    SnitchRootConfig,
    load_config,
    resolve_base_url,
    save_config,
)

app = typer.Typer(
    name="snitchroot",
    help="SnitchRoot payment rail sentry CLI.",
    no_args_is_help=True,
)
console = Console(stderr=True)

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_AUTH_OR_NETWORK = 2


def _handle_client_error(exc: Exception) -> None:
    if isinstance(exc, (AuthError, NetworkError)):
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=EXIT_AUTH_OR_NETWORK) from exc
    if isinstance(exc, ApiError):
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=EXIT_FAILURE) from exc
    raise exc


def _require_config() -> SnitchRootConfig:
    config = load_config()
    if config is None:
        console.print(
            "[red]Not logged in.[/red] Run [bold]snitchroot login <api_key>[/bold] first."
        )
        raise typer.Exit(code=EXIT_AUTH_OR_NETWORK)
    return config


@app.command()
def login(
    api_key: Annotated[str, typer.Argument(help="SnitchRoot API key (sr_...)")],
    base_url: Annotated[
        str | None,
        typer.Option("--base-url", help="API base URL (default: env or http://localhost:8000)"),
    ] = None,
) -> None:
    """Store API credentials after validating against GET /health."""
    url = resolve_base_url(base_url)
    client = SnitchRootClient(url)
    try:
        health = client.validate_login(api_key)
    except (AuthError, NetworkError, ApiError) as exc:
        _handle_client_error(exc)

    config = SnitchRootConfig(
        api_key=api_key,
        merchant_id=health.merchant_id,
        base_url=url,
    )
    path = save_config(config)
    console.print(f"Logged in as merchant [bold]{health.merchant_id}[/bold].")
    console.print(f"Config saved to [dim]{path}[/dim].")


@app.command()
def discrepancies(
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a table"),
    ] = False,
) -> None:
    """List reconciliation discrepancies (exit 1 if any open HIGH severity)."""
    config = _require_config()
    client = SnitchRootClient(config.base_url, api_key=config.api_key)
    try:
        items = client.list_discrepancies()
    except (AuthError, NetworkError, ApiError) as exc:
        _handle_client_error(exc)

    high_open = [
        d
        for d in items
        if d.severity == "HIGH" and d.status in ("open", "unresolved")
    ]

    if as_json:
        payload = [
            {
                "id": d.id,
                "severity": d.severity,
                "status": d.status,
                "description": d.description,
            }
            for d in items
        ]
        typer.echo(json.dumps(payload, indent=2))
    else:
        table = Table(title="Discrepancies")
        table.add_column("ID")
        table.add_column("Severity")
        table.add_column("Status")
        table.add_column("Description")
        for d in items:
            table.add_row(d.id, d.severity, d.status, d.description)
        console.print(table)

    if high_open:
        if not as_json:
            console.print(
                f"[red]{len(high_open)} open HIGH severity discrepancy(ies).[/red]"
            )
        raise typer.Exit(code=EXIT_FAILURE)


@app.command()
def chain(
    verify_only: Annotated[
        bool,
        typer.Option(
            "--verify-only",
            help="Exit without printing entries; fail if chain is not verified",
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON instead of a table"),
    ] = False,
) -> None:
    """Show or verify the merchant immutable evidence chain."""
    config = _require_config()
    client = SnitchRootClient(config.base_url, api_key=config.api_key)
    try:
        result = client.get_chain(config.merchant_id)
    except (AuthError, NetworkError, ApiError) as exc:
        _handle_client_error(exc)

    if not result.verified:
        if not as_json and not verify_only:
            console.print("[red]Chain verification failed (verified=false).[/red]")
        raise typer.Exit(code=EXIT_FAILURE)

    if verify_only:
        raise typer.Exit(code=EXIT_OK)

    if as_json:
        payload = {
            "verified": result.verified,
            "entries": [
                {
                    "index": e.index,
                    "hash": e.hash,
                    "timestamp": e.timestamp,
                    "event_type": e.event_type,
                }
                for e in result.entries
            ],
        }
        typer.echo(json.dumps(payload, indent=2))
    else:
        table = Table(title="Evidence chain")
        table.add_column("Index", justify="right")
        table.add_column("Event")
        table.add_column("Timestamp")
        table.add_column("Hash")
        for e in result.entries:
            table.add_row(str(e.index), e.event_type, e.timestamp, e.hash)
        console.print(table)
        console.print("[green]Chain verified.[/green]")


@app.command()
def register(
    name: Annotated[str, typer.Option("--name", help="Merchant display name")],
    key: Annotated[
        str,
        typer.Option("--key", help="Stripe restricted key (rk_...)"),
    ],
    interval: Annotated[
        int,
        typer.Option("--interval", help="Probe interval in seconds"),
    ] = 900,
    base_url: Annotated[
        str | None,
        typer.Option("--base-url", help="API base URL"),
    ] = None,
) -> None:
    """Register a new merchant and save the one-time API key."""
    url = resolve_base_url(base_url)
    client = SnitchRootClient(url)
    try:
        registration = client.register_merchant(name, key, interval)
    except (AuthError, NetworkError, ApiError) as exc:
        _handle_client_error(exc)

    config = SnitchRootConfig(
        api_key=registration.api_key,
        merchant_id=registration.merchant_id,
        base_url=url,
    )
    path = save_config(config)

    console.print(f"Merchant [bold]{registration.merchant_id}[/bold] registered.")
    console.print(f"API key: [bold]{registration.api_key}[/bold]")
    console.print(
        "[yellow]Warning:[/yellow] This API key will not be shown again. "
        "It has been saved to your config."
    )
    console.print(f"Config saved to [dim]{path}[/dim].")


def run() -> None:
    """Console script entrypoint."""
    try:
        app()
    except typer.Exit as exc:
        sys.exit(exc.exit_code)


if __name__ == "__main__":
    run()
