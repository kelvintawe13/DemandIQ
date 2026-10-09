# backend/app/schemas

## Status

**Fully implemented** — 118 py, Pydantic v2, 32 routers (`backend/app/main.py:249`), 177 paths
(213 items), `014_devices` — validates every `backend/API_ENDPOINTS.md` payload.

## Purpose & Proposal Alignment

Pydantic v2 request/response schemas validating every `API_ENDPOINTS.md` payload (177 paths →
213 items). Enforces the same invariants as `models/` but at the API boundary, before any service
or DB write. Live — see `backend/app/schemas/` and `backend/app/main.py:135`.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics, **3.16** uncertainty,
**3.17-3.20** policy SR/PS/UD, **3.21** evidence boundary, **3.22** architecture
(`backend/app/main.py:135`, `frontend/src/api/client.ts:1`).

## File Map

| Schema                                         | Validates                                                                                                                      |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `user.py` / `workspace.py` / `common.py`       | `name, email, password, role, business_name, turnstile_token`, `HealthResponse`, pagination                                    |
| `access.py`                                    | `audit_log`, `location_access_logs` reads                                                                                      |
| `me.py`                                        | `profile`, `preferences`, `notifications`, `mfa`, `sessions`, `identities`                                                     |
| `location.py` / `menu.py`                      | `locations`, `menu_groups` CRUD; admin guards                                                                                  |
| `apikeys.py` / `connectors.py` / `sources.py`  | `api-keys`, `connectors`, `sources` + `mapping`, `test`                                                                        |
| `datasets.py`                                  | `datasets`, `waste/summary` (evidence boundary, no join)                                                                       |
| `analytics.py`                                 | `analytics/summary`, `trend`, `by-location`, `breakdown`, `insights` + `reports*`                                              |
| `models.py` / `experiments.py` / `features.py` | `models/train`, `experiments`, `features/coverage`                                                                             |
| Ingest (via `services/ingestion/*`)            | `{location_id, date, demand_qty, notes}[]`, `column_map`, `CHECK(demand_qty>=0)`, no future `date`                             |
| Forecast (via `routers/forecasts.py`)          | `location_id, target_date, horizon_days, granularity` → `{point,lower,upper,coverage_target,model_version_id,as_of_timestamp}` |
| Policy (via `routers/policies.py`)             | `location_id, period, policies[], quantile` → `{surplus_sr, surplus_ps, unmet_ud}`                                             |
| `billing` (via `routers/billing.py`)           | `subscription`, `plans`, `invoices`, `payment_method`, `contact` — `003_billing`                                               |

Schemas reuse `shared/` type names so frontend (`frontend/src/api/client.ts:1`) and backend drift is
caught at compile/test time; `forecasts.point/lower/upper` always travel together with
`coverage_target`.

## Key Props / Contracts / Data Sources

Schemas enforce turnstile, `demand_qty >= 0`, `UNIQUE(location_id,date)`, `policy_kind` enum,
`severity in (critical,warning,info)`, `format in (pdf,csv,xlsx)`, HMAC events — before service
or DB.

DB invariants: `demand_observations UNIQUE(location_id,date)` (`database/schema.sql:132`),
`forecasts.as_of_timestamp` NOT NULL (`schema.sql:227`), evidence boundary `waste_observations`
with no FK (`schema.sql:277`), two-person rule `CHECK(decided_by<>requested_by)` (`schema.sql:405`),
49 tables (`schema.sql` + `AGENT_SCHEMA.sql`).

ML contracts: `predict(location_id, target_date, as_of) -> Forecast { point, lower, upper,
coverage_target, model_version_id }` and `simulate(...) -> PolicyResult { surplus_sr, surplus_ps,
unmet_ud }` per `model/README.md`.

## Related Docs

- [API Contract](../../API_ENDPOINTS.md) — 177 paths → 213 items (sync via `backend/scripts/sync_console.py`)
- [Architecture](../../ARCHITECTURE.md)
- [DB Schema](../../../database/schema.sql) — 530 lines
- [ML Contracts](../../../model/README.md)
- [Feature Spec](../../../model/FEATURE_SPEC.md)
- [Proposal](../../../project\ proposal.md)
- [API Console](../../api-console.html) — live 213 items
