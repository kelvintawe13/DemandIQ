# model/explain

## Status
**Specification only** — No source is committed yet; this folder documents the contract the implementation must satisfy (see `backend/README.md`, `model/README.md`).

## Purpose & Proposal Alignment
Explainability for Ch 3.24 (feature-importance + SHAP, predictive not causal). Writes `forecast_explanations` (`feature_name`, `feature_value`, `shap_impact` visitor units, signed) so the explanation shown later is the one generated at forecast time.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| Planned File | Role |
| --- | --- |
| `shap_values.py` | `explain(forecast_id) -> Contribution[] { feature, value, impact }`; `aggregate_importance(model_version_id) -> FeatureImportance[]`; uses SHAP TreeExplainer for LightGBM/CatBoost |

## Key Props / Contracts / Data Sources
Contract: `GET /forecasts/{id}/explanation` returns `Contribution[]`; `GET /models/{id}/importance` aggregates. UI labels attributions as predictive attribution, not causal effect, per proposal's Ch 3.24 distinction.

DB invariants referenced: `demand_observations UNIQUE(location_id,date)` (`database/schema.sql:132`), `forecasts.as_of_timestamp` NOT NULL (`schema.sql:227`), evidence boundary `waste_observations` with no FK (`schema.sql:268-271`), two-person rule `CHECK(decided_by<>requested_by)` (`schema.sql:405`).

ML contracts: `predict(location_id, target_date, as_of) -> Forecast { point, lower, upper, coverage_target, model_version_id }` and `simulate(...) -> PolicyResult { surplus_sr, surplus_ps, unmet_ud }` per `model/README.md`.

## Related Docs
- [API Contract](../../backend/API_ENDPOINTS.md)
- [Architecture](../../backend/ARCHITECTURE.md)
- [DB Schema](../../database/schema.sql)
- [ML Contracts](../README.md)
- [Feature Spec](../FEATURE_SPEC.md)
- [Proposal](../../project proposal.md)
- [API Console](../../backend/api-console.html) (relative; open in browser, mock mode)
