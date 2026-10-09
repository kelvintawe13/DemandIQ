# model/data

## Status
**Specification only** — No source is committed yet; this folder documents the contract the implementation must satisfy (see `backend/README.md`, `model/README.md`).

## Purpose & Proposal Alignment
Data loading & auditing for Ch 3.4-3.8 (Recruit 252,108 obs, 829 restaurants; URBAN-WASTE 3,321 records, 34 establishments) and Ch 3.26 leakage controls. No training code here; only ingestion into `demand_observations`/`reservations`/`waste_observations`.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| Planned File | Role |
| --- | --- |
| `load.py` | Read `demand_observations` + `reservations` filtered by `as_of`, return DataFrame; supports `load(location_id, target_date, as_of)` boundary |
| `audit.py` | Gaps (85% avg store coverage, 210 stores <80%), duplicates `UNIQUE(location_id,date)`, missingness, range checks (`demand_qty>=0`), reservation coverage 38.2% |
| `ingest_recruit.py` (planned) | Parse `air_visit_data.csv`, `air_store_info.csv`, `air_reserve.csv` (92,378 records, 314/821 restaurants) into canonical schema |
| `ingest_urban.py` (planned) | Comma-decimal → numeric, DD/MM/YYYY → DATE, `waste_kg` kg, `ordered_portions` sparse (9 obs) → `waste_observations`, CC BY 4.0 |

## Key Props / Contracts / Data Sources
Invariants: `waste_observations` has no FK to `demand_observations` (Ch 3.21, `schema.sql:268-271`); `demand_observations UNIQUE(location_id,date)` (`schema.sql:132`); `forecasts.as_of_timestamp` set at query time for reproducibility.

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
