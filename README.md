# SnitchRoot

SnitchRoot is a zero-friction payment rail sentry: it runs **zero-fee Stripe probes**, ingests signed webhooks, correlates ledger drift, and produces immutable PDF incident dossiers. Generic uptime checks cannot detect silent checkout failures or webhook-to-ledger drift; SnitchRoot validates the payment protocol itself.

## Five-minute quickstart

1. **Start the stack** (development):

   ```bash
   cp .env.example .env   # set STRIPE_RESTRICTED_KEY, STRIPE_WEBHOOK_SECRET, SECRET_KEY
   docker compose up --build
   ```

   API: `http://localhost:8000/health` · OpenAPI docs: `http://localhost:8000/docs` (non-production).

2. **Register a merchant** (restricted Stripe key only — never `sk_`):

   ```bash
   curl -s -X POST http://localhost:8000/api/v1/merchants \
     -H 'Content-Type: application/json' \
     -d '{"name":"Acme","stripe_restricted_key":"rk_test_..."}'
   ```

3. **Open the dashboard** — point your Stripe webhook endpoint at `POST /webhooks/stripe` and watch probe/reconciliation events in your configured observability stack (Slack, PagerDuty, Datadog). The in-repo frontend dashboard is optional; operators typically use push alerts.

### Production compose

```bash
export POSTGRES_PASSWORD=... SECRET_KEY=... STRIPE_RESTRICTED_KEY=... STRIPE_WEBHOOK_SECRET=...
docker compose -f docker-compose.prod.yml up --build
```

Images use the `production` Docker target (non-root UID 1001, no source bind mounts).

## Probe modes

| Mode | Behavior |
|------|----------|
| **Scheduled** | APScheduler worker fires probes at `probe_interval_seconds` (default 15m). |
| **On-demand** | Invoke the Stripe prober with a `ProbeRequest` (merchant ID + restricted key). |
| **Zero-fee** | Creates an unconfirmed `PaymentIntent`, validates schema, cancels with `abandoned` — no capture, no fee. |

Failure modes surfaced: `AUTH_FAILURE`, `INTENT_CREATION_FAILED`, `SCHEMA_MISMATCH`, `CANCEL_FAILED`, and related enums in `app.probes.models`.

## Offline chain bundle verification

Each incident dossier PDF is paired with a sidecar manifest containing a SHA-256 content hash. Verify offline:

```bash
sha256sum incident-dossier.pdf
# compare to manifest.json → dossier_hash
```

The PDF builder embeds the same hash in document metadata for independent audit trails.

## CLI reference

The `cli/` package is reserved for operator tooling (merchant import, dossier verify, probe dry-run). Commands will ship as `python -m cli ...` in a follow-on release; until then use the HTTP API and pytest-backed libraries under `backend/app/`.

| Command (planned) | Purpose |
|-------------------|---------|
| `snitchroot probe run` | Run a one-off zero-fee probe for a merchant |
| `snitchroot dossier verify` | Validate PDF + manifest hash chain |
| `snitchroot merchant register` | Register merchant from shell |

## What SnitchRoot does and doesn't test

**Does**

- Stripe restricted-key authentication and PaymentIntent create/cancel lifecycle
- Webhook signature verification, deduplication, and normalization (no CHD in logs)
- Sliding-window reconciliation between webhook events and settlement signals
- Signed PDF dossiers for drift and probe failures

**Does not**

- Storefront HTML, checkout UI, or headless browser flows
- Cardholder data (PAN, CVV, billing address) — out of PCI scope by design
- Full secret Stripe keys (`sk_`) — rejected at config and registration
- Guarantee detection of non-Stripe gateways without additional probers

## Development

```bash
pip install -e ".[dev]"
PYTHONPATH=backend pytest
ruff check backend/
mypy backend/app/
```

See `docs/ARCHITECTURE.md` for system design and `docs/STATUS.md` for module readiness.
