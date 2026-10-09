# frontend/src

## Status
**Implemented prototype** — React/TypeScript source is present and runnable via `vite`.

## Purpose & Proposal Alignment
Application source for the React SPA. Implements Ch 3.22 architecture: `App.tsx` wires React Router to the FastAPI `ForecastRequestAPI` interface; contexts provide session/theme/locale; components render forecasts with intervals and policy outcomes. Aligns with Ch 3.6 ingestion normalisation (staged uploads preview) and Ch 3.11 temporal validation (as_of boundary surfaced in UI).

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| Path | Role |
| --- | --- |
| `App.tsx` | Router + AppShell composition |
| `index.tsx` / `index.css` | Bootstrap + Tailwind entry |
| `components/` | Reusable UI (see `components/README.md`) |
| `pages/` | Route views (see `pages/FEATURES.md`) |
| `contexts/` | Global state (see `contexts/README.md`) |
| `data/` | Mock/contract data (see `data/README.md`) |
| `hooks/` | Custom hooks (see `hooks/README.md`) |
| `package.json` | Frontend dependencies |

## Key Props / Contracts / Data Sources
Data sources: `demand_observations` via `/ingest/*` and `/forecasts`; `waste_observations` never joined (Ch 3.21 boundary, Fig 4) via `GET /waste/summary` only for admin context. Feature definitions via `GET /features`.

DB invariants referenced: `demand_observations UNIQUE(location_id,date)` (`database/schema.sql:132`), `forecasts.as_of_timestamp` NOT NULL (`schema.sql:227`), evidence boundary `waste_observations` with no FK (`schema.sql:268-271`), two-person rule `CHECK(decided_by<>requested_by)` (`schema.sql:405`).

ML contracts: `predict(location_id, target_date, as_of) -> Forecast { point, lower, upper, coverage_target, model_version_id }` and `simulate(...) -> PolicyResult { surplus_sr, surplus_ps, unmet_ud }` per `model/README.md`.

## Related Docs
- [API Contract](../../backend/API_ENDPOINTS.md)
- [Architecture](../../backend/ARCHITECTURE.md)
- [DB Schema](../../database/schema.sql)
- [ML Contracts](../../model/README.md)
- [Feature Spec](../../model/FEATURE_SPEC.md)
- [Proposal](../../project proposal.md)
- [API Console](../../backend/api-console.html) (relative; open in browser, mock mode)
