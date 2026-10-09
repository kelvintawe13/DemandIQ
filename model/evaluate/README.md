# model/evaluate

## Status
**Specification only** — No source is committed yet; this folder documents the contract the implementation must satisfy (see `backend/README.md`, `model/README.md`).

## Purpose & Proposal Alignment
Evaluation for Ch 3.11 temporal validation + Ch 3.15 metrics + Ch 3.25 sensitivity. No random train-test split; chronological train→validation→held-out test, expanding/rolling-origin folds only on train. Test period never used for tuning/selection (Ch 3.14).

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| Planned File | Role |
| --- | --- |
| `metrics.py` | `mae(y,yhat)`, `rmse`, `rmsle` (log scale for right-skew mean 20.97, max 877), optional `R2` supplementary |
| `backtest.py` | `rolling_origin_splits(dates) -> Fold[]`; `evaluate_fold(model, fold) -> { mae, coverage }`; aggregates to `experiments` + `experiment_folds` |

## Key Props / Contracts / Data Sources
Sensitivity dimensions (Ch 3.25): model choice (LightGBM/CatBoost vs XGBoost/RF/linear/historical_avg), feature groups, reservation availability, uncertainty method, preparation quantile, policy definition. Results drive `change_requests` promotion (two-person rule).

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
