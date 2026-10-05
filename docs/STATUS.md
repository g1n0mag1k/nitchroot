# SnitchRoot — Implementation Status

Last updated: 2026-10-05 (launch hardening pass)

## Module readiness

| Module | Path | Status |
|--------|------|--------|
| Health API | `backend/app/api/health.py` | **IMPLEMENTED and TESTED** |
| Merchants API | `backend/app/api/merchants.py` | **IMPLEMENTED and TESTED** (in-memory stub; no DB persistence) |
| Webhooks API | `backend/app/api/webhooks.py` | **IMPLEMENTED and TESTED** |
| Stripe prober | `backend/app/probes/stripe_prober.py` | **IMPLEMENTED and TESTED** |
| Reconciliation engine | `backend/app/reconciliation/engine.py` | **IMPLEMENTED and TESTED** |
| PDF dossier builder | `backend/app/artifacts/pdf_builder.py` | **IMPLEMENTED and TESTED** |
| Config / settings | `backend/app/core/config.py` | **IMPLEMENTED and TESTED** |
| Structured logging + scrubbing | `backend/app/core/logging.py` | **IMPLEMENTED and TESTED** |
| Rate limiting | `backend/app/core/rate_limit.py` | **IMPLEMENTED and TESTED** |
| Security headers | `backend/app/middleware/security_headers.py` | **IMPLEMENTED and TESTED** |
| Redis client | `backend/app/core/redis.py` | **IMPLEMENTED and TESTED** |
| Database session factory | `backend/app/core/database.py` | **IMPLEMENTED (no tests)** |
| APScheduler worker | `backend/workers/runner.py` | **STUB** (signal-aware idle loop) |
| CLI | `cli/` | **NOT STARTED** |
| Frontend dashboard | `frontend/src/` | **NOT STARTED** |
| Production Docker image | `backend/Dockerfile` | **IMPLEMENTED (no automated image tests)** |

## Open TODO comments

| Location | Text |
|----------|------|
| `backend/app/api/webhooks.py:103` | `TODO (Prompt #5): enqueue normalized event for ledger reconciliation` |

## Known limitations

- Merchant registration returns a UUID but does not persist to PostgreSQL yet; ORM models and Alembic migrations are not wired.
- Worker process is a placeholder; scheduled probes and reconciliation jobs are not started in `workers.runner`.
- API authentication (Bearer tokens) is documented in architecture but not enforced on routes.
- `GET /health` does not check database or Redis connectivity.
- CLI and frontend directories exist as scaffolding only.
- Rate limits use in-memory slowapi storage per API process; horizontal scale requires a shared Redis-backed limiter (future work).
- Reconciliation enqueue after webhook ingest is not implemented (see TODO above).
