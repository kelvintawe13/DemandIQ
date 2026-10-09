# backend/app

## Status

**Fully implemented** — 118 py, 32 routers, 177 paths (213 items), `014_devices`,
575 tests passed + 7 skipped, `333× EmptyState` (`backend/app/main.py:249` `include_router`,
`backend/app/models/__init__.py`).

## Purpose & Proposal Alignment

FastAPI application root implementing Ch 3.22 components: Data Ingestion & Normalisation →
Forecasting Service → Policy Simulation Engine → Explainability Service, via
`backend/ARCHITECTURE.md` and `backend/API_ENDPOINTS.md` (177 paths → 213 items). Live — see
`backend/app/main.py:135` (`create_app`), routers registered at `backend/app/main.py:249`.
Persists to PostgreSQL (`database/schema.sql` 530 lines + `database/AGENT_SCHEMA.sql`) and
object store/Redis.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE),
**3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21**
URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5
(React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map

| Subfolder             | Module                                                                                                  | Live                             |
| --------------------- | ------------------------------------------------------------------------------------------------------- | -------------------------------- |
| `routers/`            | 32 routers (30 modules) — see `routers/README.md`                                                      | `backend/app/main.py:249`        |
| `services/`           | Business logic — see `services/README.md`                                                               | `ingestion/file\|image`split,`middleware/audit.py` |
| `models/`             | SQLAlchemy ORM — 41 model classes / 20 modules — see `models/README.md`                                   | `backend/app/models/__init__.py` |
| `schemas/`            | Pydantic v2 request/response — see `schemas/README.md`                                                  |                                  |
| `middleware/audit.py` | `AuditMiddleware` — `audit_log` + `location_access_logs`                                                |                                  |
| `jobs/`               | RQ/Celery workers: ingestion, training, calibration, nightly `POST /forecasts/batch`                    |                                  |
| `security/`           | JWT, TOTP, Turnstile, rate-limit, `X-DemandIQ-Signature` webhook signing                                |                                  |
| `main.py`             | App factory + 28 router registrations                                                                   | `backend/app/main.py:135`, `:249` |
| `config.py`           | `DATABASE_URL` (`docker-compose.yml:47`), `REDIS_URL`, `JWT_SECRET`, `DEFAULT_COVERAGE`, `VITE_API_URL` |                                  |
| `deps.py`             | `get_current_user`, `require_admin`, `get_db`                                                           |                                  |
| `database.py`         | Session factory                                                                                         |                                  |

Router registration is the source of truth — `backend/app/main.py:249` (`include_router` x28 +
`agent.admin_router`); console sync is `backend/scripts/sync_console.py`; client is
`frontend/src/api/client.ts:1`.

## Key Props / Contracts / Data Sources

Non-functional targets per `ARCHITECTURE.md` §8-9: forecast read p95 <300 ms, on-demand <2 s,
nightly 10k locations <20 min, webhook ack <500 ms.

DB invariants: `demand_observations UNIQUE(location_id,date)` (`database/schema.sql:132`),
`forecasts.as_of_timestamp` NOT NULL (`schema.sql:227`), evidence boundary `waste_observations`
with no FK (`schema.sql:277`), two-person rule `CHECK(decided_by<>requested_by)` (`schema.sql:405`),
49 tables (530 lines, `schema.sql`).

ML contracts: `predict(location_id, target_date, as_of) -> Forecast { point, lower, upper,
coverage_target, model_version_id }` and `simulate(...) -> PolicyResult { surplus_sr, surplus_ps,
unmet_ud }` per `model/README.md`. Frontend surfaces empty/error via `EmptyState` (333×).

## Related Docs

- [API Contract](../API_ENDPOINTS.md) — 177 paths → 213 items (openapi.json, `sync_console.py`)
- [Architecture](../ARCHITECTURE.md)
- [DB Schema](../../database/schema.sql) — 530 lines + `AGENT_SCHEMA.sql`
- [ML Contracts](../../model/README.md)
- [Feature Spec](../../model/FEATURE_SPEC.md)
- [Proposal](../../project\ proposal.md)
- [API Console](../api-console.html) — live catalog 213 items (`backend/scripts/sync_console.py`)
- [Client](../../frontend/src/api/client.ts) — `frontend/src/api/client.ts:1`
