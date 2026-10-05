# Security

## Reporting vulnerabilities

Report suspected security issues privately to **rogersandy90@gmail.com**. Please include reproduction steps, affected versions, and impact assessment. Do not open public GitHub issues for undisclosed vulnerabilities.

## Implemented controls (as of launch hardening)

| Control | Location |
|---------|----------|
| HTTP security headers (CSP, frame denial, nosniff, Permissions-Policy, no-store on API routes, production-only HSTS) | `backend/app/middleware/security_headers.py` |
| Redis-backed rate limiting with trusted-proxy-aware client keys; global Stripe webhook circuit breaker | `backend/app/core/rate_limit.py` |
| Structured log redaction (Stripe keys, webhook secrets, Bearer tokens, sensitive headers) for structlog and stdlib loggers | `backend/app/core/logging.py` |
| Stripe webhook signature verification and Redis deduplication | `backend/app/api/webhooks.py` |
| Rejection of full Stripe secret keys (`sk_`) in settings validation | `backend/app/core/config.py` |
| CI secret scanning (gitleaks full history, detect-secrets baseline) | `.github/workflows/ci.yml`, `.pre-commit-config.yaml` |
| Dependency vulnerability scanning (pip-audit) | `.github/workflows/ci.yml`, `.pip-audit-ignore` |
| Production container runs as UID 1001, runtime-only dependencies, health check | `backend/Dockerfile` |

## Out of scope for this document

Operational runbooks (key rotation, WAF, mTLS) are not defined here. See `docs/STATUS.md` for known product and security limitations still open in the codebase.
