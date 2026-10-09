# frontend/src/hooks

## Status
**Implemented prototype** — React/TypeScript source is present and runnable via `vite`.

## Purpose & Proposal Alignment
Custom React hooks for shared behaviour across pages. Small surface; currently supports dismissible banners/modals used in quality/alerts ingestion flows (Ch 3.6 staged commit, Ch 3.28 consent).

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| File | Role |
| --- | --- |
| `useDismissable.ts` | Outside-click / Escape dismiss for popovers and modals |
| `useDocumentTitle.ts` | `syncDocumentMeta({route, pageMeta})` + `useDocumentTitle()` — dynamic `document.title` + `meta[name=description]` + `og/twitter` per route; `routeTitles` for outer routes, `data/pages.ts:35 pageMeta` for inner `app` routes (`overview→analytics→...→admin-*`). Used in `App.tsx:89`. |

## Key Props / Contracts / Data Sources
Props/contract: `useDismissable(localStorageKey: string, defaultVisible?: boolean)`. No API/data source; purely client-side.

DB invariants referenced: `demand_observations UNIQUE(location_id,date)` (`database/schema.sql:132`), `forecasts.as_of_timestamp` NOT NULL (`schema.sql:227`), evidence boundary `waste_observations` with no FK (`schema.sql:268-271`), two-person rule `CHECK(decided_by<>requested_by)` (`schema.sql:405`).

ML contracts: `predict(location_id, target_date, as_of) -> Forecast { point, lower, upper, coverage_target, model_version_id }` and `simulate(...) -> PolicyResult { surplus_sr, surplus_ps, unmet_ud }` per `model/README.md`.

## Related Docs
- [API Contract](../../../backend/API_ENDPOINTS.md)
- [Architecture](../../../backend/ARCHITECTURE.md)
- [DB Schema](../../../database/schema.sql)
- [ML Contracts](../../../model/README.md)
- [Feature Spec](../../../model/FEATURE_SPEC.md)
- [Proposal](../../../project proposal.md)
- [API Console](../../backend/api-console.html) (relative; open in browser, mock mode)
