# backend/app/services

## Status

**Fully implemented** — 118 py, split `ingestion/file|image`, `forecasting/loader|conformal|persist`,
`middleware/audit.py`, `014_devices`, 32 routers (`backend/app/main.py:249`).

## Purpose & Proposal Alignment

Business-logic layer called by routers; owns transactions, leakage checks, and ML delegation.
Implements Ch 3.6-3.26 pipeline: ingestion normalisation, feature build with `as_of`,
LightGBM/CatBoost inference, adaptive conformal, SR/PS/UD simulation, SHAP, quality/alert
evaluation. Live — see `backend/app/services/` tree and `backend/app/middleware/audit.py`.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics, **3.16** uncertainty,
**3.17-3.20** policy SR/PS/UD, **3.21** evidence boundary, **3.22** architecture
(`backend/app/main.py:135`).

## File Map

| Service                                 | Responsibility                                                                                                   |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `ingestion/manual.py`                   | Validate `{location_id, date, demand_qty, notes}[]`, enforce `UNIQUE(location_id,date)` + `CHECK(demand_qty>=0)` |
| `ingestion/file.py`                     | Parse CSV/XLSX, apply `connector_mappings`, stage in `ingest_uploads`, preview 5 rows                            |
| `ingestion/image.py`                    | Queue OCR, return `job_id`, extract rows for review                                                              |
| `ingestion/normaliser.py`               | Canonical normalisation shared by manual/file/image                                                              |
| `forecasting/loader.py`                 | Load `model_versions` production artefact                                                                        |
| `forecasting/conformal.py`              | Adaptive conformal (Gibbs & Candès 2021) → `calibrations` (`target_coverage 90`)                                 |
| `forecasting/persist.py`                | Persist `forecasts` with `as_of_timestamp` + fallback `historical_avg` if <30 days                               |
| `feature_build.py`                      | Leakage-safe feature build with `as_of` (no `date >= target_date`)                                               |
| `policy/simulate.py`                    | `model/policy/simulate.py` → `policy_simulations` (`surplus_sr`, `surplus_ps`, `unmet_ud`, Ch 3.18-3.19)         |
| `explain/shap.py`                       | `model/explain/shap_values.py` → `forecast_explanations` (`feature, value, shap_impact`)                         |
| `quality.py` / `alerts`                 | Evaluate `quality_rules`/`alert_rules` → `quality_issues`/`alerts`                                               |
| `reporting/builder.py` + `scheduler.py` | Async `reports` to object store; `report_schedules` cron                                                         |
| `delivery/webhooks.py`                  | HMAC-SHA256 signing, retry 5× backoff                                                                            |
| `governance.py`                         | `change_requests` with `CHECK(decided_by<>requested_by)` (two-person rule), `audit_log` writes                   |
| `middleware/audit.py`                   | `AuditMiddleware` — logs privileged requests to `audit_log` + `location_access_logs`                             |

Services delegate to `model/` for ML: `predict(location_id, target_date, as_of) -> Forecast` and
`simulate(...) -> PolicyResult`. No feature may read `date >= target_date` (enforced by test).

## Key Props / Contracts / Data Sources

ML delegation: `predict(location_id, target_date, as_of) -> Forecast` and `simulate(...) ->
PolicyResult` per `model/README.md`; leakage boundary enforced in `feature_build.py`.

DB invariants: `demand_observations UNIQUE(location_id,date)` (`database/schema.sql:132`),
`forecasts.as_of_timestamp` NOT NULL (`schema.sql:227`), evidence boundary `waste_observations`
with no FK (`schema.sql:277`), two-person rule `CHECK(decided_by<>requested_by)` (`schema.sql:405`),
49 tables (`schema.sql` + `AGENT_SCHEMA.sql`).

Client is `frontend/src/api/client.ts:1` (`VITE_API_URL`, `EmptyState` mapping).

## Related Docs

- [API Contract](../../API_ENDPOINTS.md) — 177 paths → 213 items (`backend/scripts/sync_console.py`)
- [Architecture](../../ARCHITECTURE.md)
- [DB Schema](../../../database/schema.sql) — 530 lines
- [ML Contracts](../../../model/README.md)
- [Feature Spec](../../../model/FEATURE_SPEC.md)
- [Proposal](../../../project\ proposal.md)
- [API Console](../../api-console.html) — live 213 items
