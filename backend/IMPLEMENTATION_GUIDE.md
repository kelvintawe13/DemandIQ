# DemandIQ — Backend Implementation Guide

Concrete build order, layout and conventions. Read `ARCHITECTURE.md` first for
what the system does; this document is how to build it.

## Stack

| Concern | Choice | Why |
| --- | --- | --- |
| Language | Python 3.11 | Same runtime as the models; no serialisation boundary |
| API | FastAPI + Pydantic v2 | Typed request/response, OpenAPI for free |
| ORM | SQLAlchemy 2.0 + Alembic | Explicit migrations, no implicit schema drift |
| Store | PostgreSQL 15 | Relational integrity for the governance constraints |
| Cache / queue | Redis + RQ (or Celery) | Async ingestion, training, report generation |
| Object store | S3-compatible | Uploads, model artifacts, generated reports |
| Auth | OAuth2 password + JWT, TOTP second factor | Matches the sign-in flow already designed |
| Container | Docker + compose | Reproducible across machines |

## Layout

```
backend/
  app/
    main.py              FastAPI app, middleware, router registration
    config.py            Settings from environment, validated at boot
    deps.py              Shared dependencies: db session, current user, scopes
    routers/             One module per resource group, mirrors API_ENDPOINTS.md
    services/
      ingestion/         One adapter per channel + the shared normaliser
      forecasting/       Model loading, prediction, conformal intervals
      policy/            Preparation policies, SR / PS / UD computation
      explain/           SHAP extraction and storage
      quality/           Rule engine
      reporting/         Report builders, scheduling
      delivery/          Webhook dispatch with signing and retry
    models/              SQLAlchemy models, one module per domain
    schemas/             Pydantic request/response models
    jobs/                Queue workers and scheduled tasks
    security/            Hashing, JWT, TOTP, API-key scopes, rate limits
  migrations/            Alembic
  tests/
```

Rule: routers contain no business logic. They validate, authorise, call a
service, and shape the response.

## Build order

Each phase leaves the system runnable.

1. **Foundation** — settings, database session, migrations from
   `database/schema.sql`, health endpoint, container setup.
2. **Identity** — register, sign in, refresh, TOTP enrolment and challenge,
   password reset, sessions, API keys with scopes. Rate limit the auth routes
   before anything else is exposed.
3. **Workspace core** — workspaces, locations, members, roles. Role checks are
   a dependency, not scattered `if` statements.
4. **Ingestion** — manual entry first (simplest path to a committed row), then
   spreadsheet upload with staging and mapping, then POS/MoMo connectors, then
   OCR, then SMS. Every adapter ends at the same `commit_demand()` function so
   idempotency and source tagging cannot be bypassed.
5. **Feature build** — the `as_of` boundary, materialisation job, and the
   leakage assertion that fails the job rather than emitting a bad window.
6. **Forecasting** — load a pinned artifact, predict, calibrate, persist.
   Historical-average fallback lands here too, not later.
7. **Policy** — the three policies and both exposure metrics.
8. **Explainability** — SHAP per forecast, stored with it.
9. **Quality & alerts** — rule engine on commit, alert fan-out.
10. **Reporting & webhooks** — async generation, signed delivery with retry.
11. **Governance** — change requests, two-person rule, audit log middleware.

## Conventions

**Errors.** One shape everywhere:

```json
{ "error": { "code": "invalid_mapping", "message": "…", "field": "date", "reference": "req_01H…" } }
```

The `reference` is the request id, and it is what the UI's error page shows.

**Idempotency.** Every write that can be retried accepts an
`Idempotency-Key` header. Demand commits are additionally idempotent on
`(location_id, date)` at the database level.

**Pagination.** Cursor-based (`?cursor=&limit=`), never offset, because the
tables that matter are append-heavy.

**Time.** Everything stored in UTC. `date` on a demand row is the *service
day* in the location's timezone, resolved at the edge, never re-derived later.

**Authorisation.** Three layers, all required: a role check on the route, a
workspace scope on every query, and a location scope for members with partial
access. Never rely on the UI hiding something.

**Audit.** Middleware writes an entry for every non-GET request on a privileged
route, capturing actor, client (web / mobile / API), and before/after values.

## Environment

```
DATABASE_URL, REDIS_URL, OBJECT_STORE_URL, OBJECT_STORE_KEY/SECRET
JWT_SECRET, JWT_TTL_MINUTES, REFRESH_TTL_DAYS
TOTP_ISSUER, TURNSTILE_SECRET, GOOGLE_OAUTH_CLIENT_ID/SECRET
MODEL_REGISTRY_URI, DEFAULT_COVERAGE=0.90, DEFAULT_PREP_QUANTILE=0.80
SMTP_*, WEBHOOK_SIGNING_ALGO=hmac-sha256
```

Settings are validated at boot; a missing secret fails fast rather than at
first use.

## Scheduled work

| Job | Cadence | Notes |
| --- | --- | --- |
| Connector sync | Every 15 min | Per-connector backoff on failure |
| Feature materialisation | 03:20 daily | Must finish before forecasting |
| Recalibration | 03:40 daily | Conformal step-size update |
| Forecast publish | 04:00 daily | Emits `forecast.published` |
| Full retrain | Sundays 02:00 | Produces a candidate, never auto-promotes |
| Report delivery | 07:00 local | Per-workspace timezone |
| Drift check | Hourly | Raises an alert, does not act on its own |

## Testing

- **Unit:** normalisation adapters, policy maths, conformal update.
- **Property:** `commit_demand()` is idempotent for any replay order.
- **Leakage:** a regression test asserting no feature window includes the
  target date. This is the single most important test in the suite.
- **Contract:** the OpenAPI schema is snapshotted; a breaking change fails CI.
- **Integration:** upload → staging → commit → forecast → policy, end to end
  against a real Postgres in CI.
