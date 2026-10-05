# SnitchRoot — System Architecture

> **Classification:** Internal Engineering Reference  
> **Status:** Living Document — updated with each architectural decision  
> **Last Updated:** 2026-10-04

---

## 1. Problem Statement

Generic uptime monitors (Pingdom, UptimeRobot) issue HTTP GETs and declare a
service "up" on any 2xx response. They are structurally blind to two failure
modes that silently drain merchant revenue:

| Failure Mode | What Breaks | What Generic Monitors See |
|---|---|---|
| **Silent Checkout Drop** | Payment gateway API stops issuing `checkout.Session` or `PaymentIntent` objects — API tokens expired, quota exceeded, gateway config corrupted | HTTP 200 from the storefront marketing page |
| **Webhook-to-Ledger Drift** | Shopify/custom cart marks order `complete`; Stripe `payment_intent.succeeded` webhook fires; but `BalanceTransaction` never settles — funds are stuck in dispute, refunded, or never captured | HTTP 200 from order confirmation page |

SnitchRoot solves both with **zero live charges** and **zero headless browser
fragility**.

---

## 2. Core Design Principles

1. **Zero Cardholder Data (CHD) Scope.** SnitchRoot never touches card numbers,
   CVVs, or billing addresses. It interacts exclusively with Stripe's
   server-side API using restricted keys. This eliminates PCI DSS scope
   entirely — see `docs/COMPLIANCE_AND_PCI.md`.
2. **Zero-Fee Probing.** The probe creates a `PaymentIntent` with
   `confirm=False` and immediately cancels it with
   `cancellation_reason='abandoned'`. Stripe does not charge processing fees on
   unconfirmed, cancelled intents. Cost per probe: **$0.00**.
3. **No Headless Browsers.** All validation is at the protocol layer. No
   Playwright, no Puppeteer, no Chromium. Zero Cloudflare/DataDome exposure.
4. **Immutable Incident Evidence.** Every detected failure produces a
   cryptographically signed PDF dossier with a SHA-256 content hash embedded in
   the PDF metadata. The hash is also written to a sidecar `manifest.json` for
   independent verification.
5. **Push-First Observability.** SnitchRoot emits structured webhook events to
   the tools operators already use (Datadog, Slack, PagerDuty) rather than
   forcing adoption of a proprietary dashboard.

---

## 3. System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                        SnitchRoot System                        │
│                                                                 │
│  ┌──────────────┐    ┌──────────────────────────────────────┐  │
│  │   APScheduler│    │         FastAPI Application          │  │
│  │   Workers    │    │                                      │  │
│  │              │    │  POST /webhooks/stripe               │  │
│  │  probe_runner│    │  POST /webhooks/shopify              │  │
│  │  recon_runner│    │  GET  /health                        │  │
│  └──────┬───────┘    └──────────────┬───────────────────────┘  │
│         │                           │                           │
│         ▼                           ▼                           │
│  ┌──────────────┐    ┌──────────────────────────────────────┐  │
│  │ Stripe Prober│    │       Webhook Ingestion Layer        │  │
│  │              │    │   (signature verify → normalize      │  │
│  │ 1. Auth with │    │    → Redis dedup → DB persist)       │  │
│  │    restricted│    └──────────────┬───────────────────────┘  │
│  │    key        │                  │                           │
│  │ 2. Create PI  │    ┌─────────────▼──────────────────────┐   │
│  │    (unconfirm)│    │      Reconciliation Engine         │   │
│  │ 3. Cancel PI  │    │   sliding-window correlator:       │   │
│  │    (abandoned)│    │   WebhookEvent ↔ BalanceTransaction│   │
│  │ 4. Assert     │    │   → drift detection                │   │
│  │    response   │    └─────────────┬──────────────────────┘   │
│  │    schema     │                  │                           │
│  └──────┬────────┘    ┌─────────────▼──────────────────────┐   │
│         │             │      Incident Dossier Builder       │   │
│         └────────────►│   (reportlab PDF + SHA-256 sign)   │   │
│                        └─────────────┬──────────────────────┘   │
│                                      │                           │
│                        ┌─────────────▼──────────────────────┐   │
│                        │       Alerting Dispatcher          │   │
│                        │  Slack │ PagerDuty │ Datadog │ WH  │   │
│                        └────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
         │                           │
         ▼                           ▼
   ┌──────────┐               ┌──────────┐
   │ Stripe   │               │PostgreSQL│
   │ API      │               │+ Redis   │
   └──────────┘               └──────────┘
```

---

## 4. Zero-Fee Probe Lifecycle (State Machine)

```
IDLE
  │
  │  scheduled interval fires
  ▼
AUTHENTICATING
  │  GET /v1/payment_intents (auth test with restricted key)
  │  Error → PROBE_FAILED(auth_error) → emit alert → IDLE
  ▼
CREATING_INTENT
  │  POST /v1/payment_intents
  │    amount=100, currency=usd, confirm=False,
  │    metadata={probe_id, merchant_id, timestamp}
  │  Error → PROBE_FAILED(creation_error) → emit alert → IDLE
  ▼
VALIDATING_SCHEMA
  │  Assert: id starts with "pi_"
  │  Assert: status == "requires_payment_method"
  │  Assert: client_secret present
  │  Assert: metadata round-trips correctly
  │  Schema mismatch → PROBE_FAILED(schema_drift) → emit alert → IDLE
  ▼
CANCELLING_INTENT
  │  POST /v1/payment_intents/{id}/cancel
  │    cancellation_reason=abandoned
  │  Error → PROBE_FAILED(cancel_error) → emit alert
  │  (intent may persist — safe; Stripe auto-expires uncaptured intents)
  ▼
PROBE_SUCCEEDED
  │  Record latency, intent_id, idempotency_key to DB
  │  Emit structured log (no CHD)
  ▼
IDLE
```

**Cost Analysis:**

| Action | Stripe Fee |
|---|---|
| Create `PaymentIntent` (`confirm=False`) | $0.00 |
| Cancel `PaymentIntent` (`abandoned`) | $0.00 |
| **Total per probe cycle** | **$0.00** |

Stripe only charges fees on `capture` or `confirm` with a payment method
attached. This probe never attaches a payment method.

---

## 5. Reconciliation Engine — Sliding Window Algorithm

```
T=0                                    T=window_size (default: 2h)
│                                                                │
│   WebhookEvents (order.completed, payment_intent.succeeded)  │
│   ──────────────────────────────────────────────────────────► │
│                                                                │
│   BalanceTransactions (Stripe /v1/balance_transactions)       │
│   ──────────────────────────────────────────────────────────► │
│                                                                │
│   Join key: payment_intent_id                                  │
│                                                                │
│   For each WebhookEvent with type in SUCCEEDED_TYPES:         │
│     IF no matching BalanceTransaction.net > 0                  │
│       AND event.created < NOW() - SETTLEMENT_LAG (15 min)     │
│     THEN → ReconDiscrepancy(severity=HIGH)                     │
└────────────────────────────────────────────────────────────────┘
```

The 15-minute settlement lag prevents false positives during normal Stripe
processing time.

---

## 6. Data Model Overview

```
merchants
  id (UUID PK)
  name
  stripe_restricted_key_enc (AES-256-GCM encrypted at rest)
  probe_interval_seconds
  created_at

probe_results
  id (UUID PK)
  merchant_id (FK)
  status  (succeeded | failed)
  failure_mode (nullable enum)
  stripe_intent_id (nullable)
  idempotency_key
  latency_ms
  raw_response_hash (SHA-256 of response body — no CHD)
  probed_at

webhook_events
  id (UUID PK)
  merchant_id (FK)
  stripe_event_id (UNIQUE — dedup key)
  event_type
  payment_intent_id (nullable)
  amount (integer cents)
  currency
  received_at

recon_discrepancies
  id (UUID PK)
  merchant_id (FK)
  webhook_event_id (FK)
  severity (HIGH | MEDIUM | LOW)
  description
  dossier_path (nullable)
  dossier_hash (nullable)
  resolved_at (nullable)
  detected_at

alert_deliveries
  id (UUID PK)
  discrepancy_id (FK)
  channel (slack | pagerduty | datadog | webhook)
  delivered_at (nullable)
  attempt_count
  last_error (nullable)
```

---

## 7. Security Boundaries

| Boundary | Control |
|---|---|
| Stripe key storage | AES-256-GCM encrypted in DB; key derived from `SECRET_KEY` env var via PBKDF2 |
| Stripe key scope | Restricted keys — only `PaymentIntents: write`, `Balance: read` |
| Webhook ingestion | Stripe-Signature header verified via `stripe.Webhook.construct_event` |
| No CHD in logs | Raw Stripe responses hashed (SHA-256) before any storage; raw body never persisted |
| PCI scope | Zero — no cardholder data handled anywhere in the stack |
| API authentication | Bearer token (Phase 10); restricted to HTTPS in production |

---

## 8. Infrastructure Topology (Production Target)

```
                    ┌─────────────────┐
                    │   Cloudflare    │
                    │   (TLS term,    │
                    │    DDoS, WAF)   │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  Load Balancer  │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────▼───┐  ┌───────▼────┐  ┌─────▼──────┐
     │  API Pod   │  │  API Pod   │  │ Worker Pod │
     │ (FastAPI)  │  │ (FastAPI)  │  │ (APSched)  │
     └────────────┘  └────────────┘  └────────────┘
              │                             │
     ┌────────▼─────────────────────────────▼──────┐
     │          PostgreSQL (primary + replica)      │
     └──────────────────────────────────────────────┘
              │
     ┌────────▼────────┐
     │  Redis Cluster  │
     │  (dedup + jobs) │
     └─────────────────┘
```

---

## 9. Decision Log

See `docs/DECISIONS.md` for full Architecture Decision Records (ADRs).

| ADR | Decision | Rationale |
|---|---|---|
| ADR-001 | APScheduler over Celery | Celery requires a broker + result backend; APScheduler runs in-process with Redis job store for this scale; swap path is clear |
| ADR-002 | `reportlab` over WeasyPrint | No system dependency on wkhtmltopdf/Chrome; fully Python-native; adequate for compliance dossier format |
| ADR-003 | `asyncpg` + SQLAlchemy 2.0 | Native async PostgreSQL driver; SQLAlchemy 2.0 async session avoids thread-pool overhead |
| ADR-004 | SHA-256 in PDF metadata | Enables third-party verification without SnitchRoot infrastructure; tamper-evident without requiring a blockchain |
| ADR-005 | `structlog` over `logging` | JSON-first output; zero reconfiguration for Datadog Log Management ingestion |
