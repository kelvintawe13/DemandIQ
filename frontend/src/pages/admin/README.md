# frontend/src/pages/admin

## Status
**Implemented prototype (admin console)** — React/TypeScript source is present and runnable via `vite`.

## Purpose & Proposal Alignment
Administrator console for Ch 3.12-3.16 models, Ch 3.17-3.20 policies, Ch 3.21 waste evidence, and Ch 3.22 governance (change_requests two-person rule, `database/schema.sql:405`). All routes are admin-only per `API_ENDPOINTS.md` § Models/Experiments/Governance. See `ADMIN_FEATURES.md` for per-screen detail.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| File | Purpose | Key API |
| --- | --- | --- |
| `ModelRegistry.tsx` | Registry `algorithm, MAE/RMSE/RMSLE, status` | `GET /models`, `GET /models/{id}` |
| `Experiments.tsx` | Published backtests + tuning trials (read-only; `POST /experiments` is 501) | `GET /experiments`, `GET /experiments/{id}`, `POST /experiments/{id}/submit` |
| `FeatureStore.tsx` | Feature defs + coverage + leakage_safe | `GET /features`, `GET /features/{name}/coverage` |
| `ForecastExplainer.tsx` | SHAP `feature, value, impact` | `GET /forecasts/{id}/explanation`, `GET /models/{id}/importance` |
| `PolicyLab.tsx` | Simulate `surplus_sr/ps`, `unmet_ud` trade | `POST /policies/simulate`, `GET /policies` |
| `Connectors.tsx` / `LocationRegistry.tsx` | `connectors` + `connector_mappings` + `locations` | `GET /connectors`, `POST /connectors/{id}/test`, `GET /locations` |
| `DatasetsAdmin.tsx` | Recruit vs URBAN-WASTE manifests | `GET /datasets`, `GET /datasets/{id}/manifest`, `GET /waste/summary` |
| `AiGovernance.tsx` / `Approvals.tsx` | Change requests, two-person rule | `GET /change-requests`, `POST /change-requests/{id}/approve` |
| `PipelineHealth.tsx` | `pipeline_runs`, drift, schedule | `GET /pipeline/runs`, `GET /pipeline/drift` |
| `AccessControl.tsx` / `Webhooks.tsx` | Members/roles + webhooks | `GET /members`, `GET /webhooks`, `GET /audit-log` |

## Key Props / Contracts / Data Sources
Governance invariant: `change_requests.decided_by <> requested_by` (`schema.sql:405`) surfaced as disabled Approve on own request. Forecast rows always show `model_version_id` + `feature_set_version` + `as_of_timestamp`.

DB invariants referenced: `demand_observations UNIQUE(location_id,date)` (`database/schema.sql:132`), `forecasts.as_of_timestamp` NOT NULL (`schema.sql:227`), evidence boundary `waste_observations` with no FK (`schema.sql:268-271`), two-person rule `CHECK(decided_by<>requested_by)` (`schema.sql:405`).

ML contracts: `predict(location_id, target_date, as_of) -> Forecast { point, lower, upper, coverage_target, model_version_id }` and `simulate(...) -> PolicyResult { surplus_sr, surplus_ps, unmet_ud }` per `model/README.md`.

## Related Docs
- [API Contract](../../../../backend/API_ENDPOINTS.md)
- [Architecture](../../../../backend/ARCHITECTURE.md)
- [DB Schema](../../../../database/schema.sql)
- [ML Contracts](../../../../model/README.md)
- [Feature Spec](../../../../model/FEATURE_SPEC.md)
- [Proposal](../../../../project proposal.md)
- [API Console](../../backend/api-console.html) (relative; open in browser, mock mode)
