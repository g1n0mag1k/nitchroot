"""Local CLI configuration (~/.config/snitchroot/config.json)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_BASE_URL = "http://localhost:8000"
CONFIG_ENV_VAR = "SNITCHROOT_CONFIG"
BASE_URL_ENV_VAR = "SNITCHROOT_API_URL"


@dataclass
class SnitchRootConfig:
    """Persisted credentials and API base URL."""

    api_key: str
    merchant_id: str
    base_url: str = DEFAULT_BASE_URL


def config_path() -> Path:
    """Return the path to the CLI config file."""
    override = os.environ.get(CONFIG_ENV_VAR)
    if override:
        return Path(override)
    return Path.home() / ".config" / "snitchroot" / "config.json"


def load_config() -> SnitchRootConfig | None:
    """Load config from disk, or None if missing or invalid."""
    path = config_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    api_key = data.get("api_key")
    merchant_id = data.get("merchant_id")
    if not api_key or not merchant_id:
        return None
    base_url = data.get("base_url") or os.environ.get(BASE_URL_ENV_VAR) or DEFAULT_BASE_URL
    return SnitchRootConfig(
        api_key=str(api_key),
        merchant_id=str(merchant_id),
        base_url=str(base_url).rstrip("/"),
    )


def save_config(config: SnitchRootConfig) -> Path:
    """Write config atomically and return the config path."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(config)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def resolve_base_url(explicit: str | None = None) -> str:
    """Resolve API base URL from flag, env, config, or default."""
    if explicit:
        return explicit.rstrip("/")
    env_url = os.environ.get(BASE_URL_ENV_VAR)
    if env_url:
        return env_url.rstrip("/")
    loaded = load_config()
    if loaded:
        return loaded.base_url
    return DEFAULT_BASE_URL
