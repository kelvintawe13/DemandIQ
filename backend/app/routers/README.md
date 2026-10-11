# backend/app/routers

## Status

**Fully implemented** — 118 py, 32 routers, 177 paths (213 items) (`backend/app/main.py:249`
`include_router`, `grep prefix=` below). No mock routes; live catalog is `api-console.html` (187
items, `backend/scripts/sync_console.py`).

## Purpose & Proposal Alignment

One FastAPI router per resource group, each enforcing auth/role and delegating to `services/`.
Maps 1:1 to `backend/API_ENDPOINTS.md` (177 paths → 213 items). No ORM queries in routers; they
only validate `schemas/` and call services.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics, **3.16** uncertainty,
**3.17-3.20** policy SR/PS/UD, **3.21** evidence boundary, **3.22** architecture
(`backend/app/main.py:135`).

## File Map

Live — `grep prefix= backend/app/routers/*.py`; registration at `backend/app/main.py:249`:

| Module           | Prefix                                                                       | Auth                              | Paths |
| ---------------- | ---------------------------------------------------------------------------- | --------------------------------- | ----- |
| `auth.py`        | `/api/v1/auth/*`, `/auth/me`                                                 | Public + JWT                      | 11    |
| `me.py`          | `/api/v1/me/*`                                                               | JWT                               | 11    |
| `health.py`      | `/health`, `/api/v1/health`                                                  | Public                            | 2     |
| `access.py`      | `/api/v1/locations/access-logs`, `/api/v1/audit-log`                         | JWT/admin                         | 2     |
| `ingest.py`      | `/api/v1/ingest/*`                                                           | JWT; file/image multipart         | 8     |
| `forecasts.py`   | `/api/v1/forecasts`, `/forecasts/batch`, `/{id}/explanation[/summary]`, `/{id}/policy/simulation`, `/{id}/drift/monitoring` | JWT                               | 9     |
| `policies.py`    | `/api/v1/policies`, `/policies/simulate`                                     | JWT; PATCH admin → change_request | 3     |
| `quality.py`     | `/api/v1/quality/*`                                                          | JWT                               | 5     |
| `alerts.py`      | `/api/v1/alerts*`                                                            | JWT                               | 6     |
| `analytics.py`   | `/api/v1/analytics/*`                                                        | JWT                               | 5     |
| `reports.py`     | `/api/v1/reports*`                                                           | JWT                               | 6     |
| `webhooks.py`    | `/api/v1/webhooks*`                                                          | Admin; HMAC-SHA256                | 5     |
| `sources.py`     | `/api/v1/sources*`                                                           | JWT                               | 3     |
| `connectors.py`  | `/api/v1/connectors*`                                                        | Admin                             | 5     |
| `apikeys.py`     | `/api/v1/api-keys*`                                                          | Admin                             | 4     |
| `governance.py`  | `/api/v1/change-requests`, `/members`, `/roles`, `/audit-log`, `/pipeline/*` | Admin                             | 11    |
| `billing.py`     | `/api/v1/billing/*`                                                          | JWT                               | 10    |
| `agent.py`       | `/api/v1/agent/*` + `/api/v1/admin/agent/*`                                  | JWT/Admin                         | 14    |
| `models.py`      | `/api/v1/models*`                                                            | Admin                             | 7     |
| `experiments.py` | `/api/v1/experiments*`                                                       | Admin                             | 4     |
| `features.py`    | `/api/v1/features*`                                                          | Admin                             | 2     |
| `datasets.py`    | `/api/v1/datasets`, `/api/v1/waste/summary` (`waste_router`)                 | Admin                             | 3     |
| `locations.py`   | `/api/v1/locations`                                                          | JWT; POST/PATCH/DELETE admin      | 5     |
| `menu.py`        | `/api/v1/menu-groups`                                                        | JWT; POST/PATCH/DELETE admin      | 3     |

32 routers = 30 modules + `agent.admin_router` + `datasets.waste_router`;
registered at `backend/app/main.py:249` (`include_router` x28). OpenAPI:
177 paths → 213 items (`openapi.json`, `backend/scripts/sync_console.py`).

## Key Props / Contracts / Data Sources

Key invariants enforced before service call: `turnstile_token` on register/login,
`UNIQUE(location_id,date)` on ingest, `policy_kind` enum, and `forecasts.as_of_timestamp` set
server-side (never client-supplied).

DB invariants: `demand_observations UNIQUE(location_id,date)` (`database/schema.sql:132`),
`forecasts.as_of_timestamp` NOT NULL (`schema.sql:227`), evidence boundary `waste_observations`
with no FK (`schema.sql:277`), two-person rule `CHECK(decided_by<>requested_by)` (`schema.sql:405`),
49 tables (`schema.sql`).

ML contracts: `predict(location_id, target_date, as_of) -> Forecast { point, lower, upper,
coverage_target, model_version_id }` and `simulate(...) -> PolicyResult { surplus_sr, surplus_ps,
unmet_ud }` per `model/README.md`. Frontend client is `frontend/src/api/client.ts:1`.

## Related Docs

- [API Contract](../../API_ENDPOINTS.md) — 177 paths → 213 items
- [Architecture](../../ARCHITECTURE.md)
- [DB Schema](../../../database/schema.sql) — 530 lines
- [ML Contracts](../../../model/README.md)
- [Feature Spec](../../../model/FEATURE_SPEC.md)
- [Proposal](../../../project\ proposal.md)
- [API Console](../../api-console.html) — live 213 items (`sync_console.py`)
