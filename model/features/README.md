# model/features

## Status
**Specification only** — No source is committed yet; this folder documents the contract the implementation must satisfy (see `backend/README.md`, `model/README.md`).

## Purpose & Proposal Alignment
Feature engineering implementing Ch 3.10 + `FEATURE_SPEC.md` exactly. Every feature carries `feature_set_version` (e.g. `fs-2026.09.1`) stored on `forecasts` and `model_versions`; mismatch is a hard error. Information boundary `as_of = t-1 @ 23:59`, no feature reads `date >= t`.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| Planned File | Role |
| --- | --- |
| `build.py` | `build(location_id, target_date, as_of) -> FeatureRow`; implements `s.shift(1).rolling(7).mean()` and all `FEATURE_SPEC.md` definitions |
| `registry.py` | Sync `feature_definitions` table (`name, feature_group, dtype, leakage_safe, coverage_pct, in_production`) |

## Key Props / Contracts / Data Sources
Feature groups: historical (`lag_1/7/14`, `roll_mean_7/28`, `roll_std_28`, `dow_mean_8w`, `trend_7_28`), calendar (`dow`, `is_weekend`, `month`, `is_holiday`, `day_before/after_holiday`, `is_month_end`), location (`service_type`, `district`), reservation supplementary (`reserved_count`, `reserve_lead_mean`, `has_reservation_source` 38.2% coverage). Excluded: `same_day_transactions`, `weather_actual`, `next_day_reservations`, `waste_kg` (never joined).

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
