# model/policy

## Status
**Specification only** — No source is committed yet; this folder documents the contract the implementation must satisfy (see `backend/README.md`, `model/README.md`).

## Purpose & Proposal Alignment
Preparation-policy simulation for Ch 3.17-3.20. Converts forecasts into visitor-equivalent preparation quantities and reports the SR/PS/UD trade-off defined in `backend/ARCHITECTURE.md` §4 and `database/schema.sql` `policy_simulations`. Policies are transparent, no hidden optimisation.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| Planned File | Role |
| --- | --- |
| `simulate.py` | `simulate(location_id, period, policies, quantile) -> PolicyResult[]`; policies: `historical_average(P=mean last n)`, `point_forecast(P=D̂)`, `uncertainty_aware(P=Q(q))`; computes `SR=max(0,P-D̂)`, `PS=max(0,P-D)`, `UD=max(0,D-P)` |

## Key Props / Contracts / Data Sources
Key trade: raising `q` reduces `UD` but increases `PS` (Ch 3.17). Reporting both `SR` and `PS` avoids trivial `SR=0` for point policy (Ch 3.18). Results persisted to `policy_simulations` (`prepared_total`, `surplus_sr/ps`, `unmet_ud`, `surplus_days`, `unmet_days`) and returned by `POST /policies/simulate`.

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
