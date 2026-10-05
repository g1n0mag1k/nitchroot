# SnitchRoot

Zero-friction payment rail sentry and automated ledger reconciliation sentinel.

## Install CLI

The `snitchroot` command-line tool lives under `cli/` and shares dependencies with the main API package via a path reference to the repository root.

From the repository root:

```bash
# Install API + CLI (editable)
pip install -e ".[dev]"
pip install -e "./cli[dev]"
```

Or with [uv](https://github.com/astral-sh/uv):

```bash
uv pip install -e ".[dev]"
uv pip install -e "./cli[dev]"
```

Configure the API base URL (optional; defaults to `http://localhost:8000`):

```bash
export SNITCHROOT_API_URL=https://your-snitchroot-host.example
```

### Commands

| Command | Description |
| --- | --- |
| `snitchroot login <api_key>` | Validate credentials via `GET /health` and save `~/.config/snitchroot/config.json` |
| `snitchroot discrepancies [--json]` | List discrepancies; exits `1` if any open **HIGH** severity items exist |
| `snitchroot chain [--verify-only] [--json]` | Print or verify the merchant evidence chain |
| `snitchroot register --name <n> --key <rk_...> [--interval 900]` | Register a merchant and persist the one-time API key |

Exit codes: `0` success, `1` operational failure (e.g. open HIGH discrepancies, chain not verified), `2` auth or network errors.

Run CLI tests (mocked HTTP only):

```bash
cd cli && pytest
```
