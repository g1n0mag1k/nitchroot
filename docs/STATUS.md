# SnitchRoot — Implementation Status

Last updated: 2026-10-05 (launch hardening pass)

## Module readiness

| Module | Path | Status |
|--------|------|--------|
| Health API | `backend/app/api/health.py` | **IMPLEMENTED and TESTED** |
| Webhooks API | `backend/app/api/webhooks.py` | **IMPLEMENTED and TESTED** |
| Stripe prober | `backend/app/probes/stripe_prober.py` | **IMPLEMENTED and TESTED** |
| Reconciliation engine | `backend/app/reconciliation/engine.py` | **IMPLEMENTED and TESTED** |
| PDF dossier builder | `backend/app/artifacts/pdf_builder.py` | **IMPLEMENTED and TESTED** |
| Config / settings | `backend/app/core/config.py` | **IMPLEMENTED and TESTED** |
| Structured logging + scrubbing | `backend/app/core/logging.py` | **IMPLEMENTED and TESTED** |
| Rate limiting (Redis) | `backend/app/core/rate_limit.py` | **IMPLEMENTED and TESTED** |
| Security headers | `backend/app/middleware/security_headers.py` | **IMPLEMENTED and TESTED** |
| Redis client | `backend/app/core/redis.py` | **IMPLEMENTED and TESTED** |
| Database session factory | `backend/app/core/database.py` | **IMPLEMENTED (no tests)** |
| APScheduler worker | `backend/workers/runner.py` | **STUB** (signal-aware idle loop) |
| Production Docker image | `backend/Dockerfile` | **IMPLEMENTED (no automated image tests)** |

## Open TODO comments

| Location | Text |
|----------|------|
| `backend/app/api/webhooks.py` | `TODO (Prompt #5): enqueue normalized event for ledger reconciliation` |

## Known limitations

- **Reconciliation model**: engine compares probe results to webhook events; production ledger reconciliation requires balance transactions and settlement signals.
- **PDF hashing**: footer/metadata hash is computed before final PDF bytes are complete; offline verification against the shipped file can disagree.
- **Webhook persistence**: verified events are not written to PostgreSQL or a queue after normalization.
- **Async boundary**: Stripe Python SDK calls block inside `async` route handlers and the prober.
- **Stripe credentials**: one global `STRIPE_RESTRICTED_KEY` and `STRIPE_WEBHOOK_SECRET`; no per-merchant encrypted storage or API for registering keys.
- **Database**: no ORM models, migrations, or merchant persistence; `POST /api/v1/merchants` was removed until encrypted storage exists.
- **Worker process**: placeholder loop only; scheduled probes and reconciliation jobs are not started.
- **API authentication**: Bearer tokens described in architecture are not enforced on routes.
- **`GET /health`**: does not check database or Redis connectivity.
