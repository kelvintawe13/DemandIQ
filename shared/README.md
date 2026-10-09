# shared

## Status
**Type contracts only** — `index.ts` declares the isomorphic TypeScript types
(`User`, `Location`, `Forecast`, `DemandObservation`, `Alert`, `QualityIssue` and
the role/connector/upload/model enum unions). There is no runtime code, no
component, and no build output: the package is `noEmit` and consumed as source.

Enforced by `make typecheck-shared` (`tsc --noEmit` against `shared/tsconfig.json`,
strict), which is part of `make verify` and CI.

> Nothing imports this package yet. `frontend/src/data/` and
> `frontend/src/lib/api.ts` currently hold their own copies of these shapes, so
> until the two are reconciled this is a reference contract, not the single
> source of truth.

## Purpose & Proposal Alignment
Isomorphic TypeScript contracts shared by frontend (and future mobile) to mirror `backend/app/schemas` (Pydantic) and `database/schema.sql` enums. Prevents drift between `forecasts.point/lower/upper`, `policy_kind`, `model_status`, and the UI. Aligns with Ch 3.31 reproducibility (pinned types).

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| Path | Role |
| --- | --- |
| `index.ts` | All domain types and enum unions, declared directly (no barrel re-export yet) |
| `types/` | Empty placeholder — reserved for a split-out domain types module |
| `utils/` | Empty placeholder — reserved for formatters / date helpers (Africa/Kigali) |
| `config/` | Empty placeholder — reserved for shared constants (`DEFAULT_COVERAGE=0.90`, `DEFAULT_PREP_QUANTILE=0.80`) |
| `tsconfig.json` | Strict, `noEmit`; the include list already covers the three directories above, so files added there are gated |
| `package.json` | Package manifest |

## Key Props / Contracts / Data Sources
Contracts: `Forecast { point, lower, upper, coverage_target, model_version_id, as_of_timestamp }` matches `forecasts` table; `PolicyResult { surplus_sr, surplus_ps, unmet_ud }` matches `policy_simulations`; `model_status` enum matches `database/schema.sql:157`.

DB invariants referenced: `demand_observations UNIQUE(location_id,date)` (`database/schema.sql:132`), `forecasts.as_of_timestamp` NOT NULL (`schema.sql:227`), evidence boundary `waste_observations` with no FK (`schema.sql:268-271`), two-person rule `CHECK(decided_by<>requested_by)` (`schema.sql:405`).

ML contracts: `predict(location_id, target_date, as_of) -> Forecast { point, lower, upper, coverage_target, model_version_id }` and `simulate(...) -> PolicyResult { surplus_sr, surplus_ps, unmet_ud }` per `model/README.md`.

## Related Docs
- [API Contract](../backend/API_ENDPOINTS.md)
- [Architecture](../backend/ARCHITECTURE.md)
- [DB Schema](../database/schema.sql)
- [ML Contracts](../model/README.md)
- [Feature Spec](../model/FEATURE_SPEC.md)
- [Proposal](../project proposal.md)
- [API Console](../backend/api-console.html) (served at `/console`)
