# SnitchRoot

SnitchRoot is a zero-friction payment rail sentry: it runs **zero-fee Stripe probes**, ingests Stripe webhooks, correlates ledger drift, and produces PDF incident dossiers. Generic uptime checks cannot detect silent checkout failures or webhook-to-ledger drift; SnitchRoot validates the payment protocol itself.

## Five-minute quickstart

1. **Start the stack** (development):

   ```bash
   cp .env.example .env   # set STRIPE_RESTRICTED_KEY, STRIPE_WEBHOOK_SECRET, SECRET_KEY
   docker compose up --build
   ```

   API: `http://localhost:8000/health` · OpenAPI docs: `http://localhost:8000/docs` (non-production).

2. **Configure Stripe webhooks** — point your Stripe webhook endpoint at `POST /webhooks/stripe` and monitor probe/reconciliation events in your observability stack (Slack, PagerDuty, Datadog).

### Production compose

```bash
export POSTGRES_PASSWORD=... SECRET_KEY=... STRIPE_RESTRICTED_KEY=... STRIPE_WEBHOOK_SECRET=...
docker compose -f docker-compose.prod.yml up --build
```

Images use the `production` Docker target (non-root UID 1001, no source bind mounts, no dev dependencies, no `--reload`).

## Probe modes

| Mode | Behavior |
|------|----------|
| **Scheduled** | APScheduler worker fires probes at `probe_interval_seconds` (default 15m). |
| **On-demand** | Invoke the Stripe prober with a `ProbeRequest` (merchant ID + restricted key). |
| **Zero-fee** | Creates an unconfirmed `PaymentIntent`, validates schema, cancels with `abandoned` — no capture, no fee. |

Failure modes surfaced: `AUTH_FAILURE`, `INTENT_CREATION_FAILED`, `SCHEMA_MISMATCH`, `CANCEL_FAILED`, and related enums in `app.probes.models`.

## What SnitchRoot does and doesn't test

**Does**

- Stripe restricted-key authentication and PaymentIntent create/cancel lifecycle
- Webhook signature verification, deduplication, and normalization (no CHD in logs)
- Sliding-window reconciliation between webhook events and settlement signals
- PDF dossiers for drift and probe failures (content hash metadata only; see limitations)

**Does not**

- Storefront HTML, checkout UI, or headless browser flows
- Cardholder data (PAN, CVV, billing address)
- Full secret Stripe keys (`sk_`) — rejected at configuration time
- Guarantee detection of non-Stripe gateways without additional probers
- Accept merchant Stripe keys over HTTP until encrypted persistence exists

## Known limitations

See `docs/STATUS.md` for the full list. Highlights:

- Reconciliation currently compares probes to webhooks (incorrect model for production ledger matching).
- PDF footer hash does not equal the final file hash after metadata is written.
- Webhook handler does not persist normalized events yet.
- Stripe API calls run synchronously inside async handlers.
- Single global Stripe restricted key and webhook secret (no per-merchant secrets yet).
- No database models or Alembic migrations wired to the API.

## Development

```bash
pip install -e ".[dev]"
PYTHONPATH=backend pytest
ruff check backend/
mypy backend/app/
```

See `docs/ARCHITECTURE.md` for system design, `docs/STATUS.md` for module readiness, and `docs/SECURITY.md` for security reporting and implemented controls.
