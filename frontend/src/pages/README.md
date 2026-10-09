# frontend/src/pages

## Status
**Implemented prototype** — React/TypeScript source is present and runnable via `vite`.

## Purpose & Proposal Alignment
Route views implementing Ch 3.4-3.11 user-facing features and Fig 6-7 UML flows. Each page maps to one or more API resource groups. Detailed per-page docs: `FEATURES.md` (9 major pages) + `admin/ADMIN_FEATURES.md` (admin console). Evidence boundary (Fig 4, Ch 3.21) enforced: no page joins `demand_observations` with `waste_observations`.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| File | Route | API Group |
| --- | --- | --- |
| `Overview.tsx` | `/overview` | `GET /analytics/summary`, `/forecasts` |
| `Analytics.tsx` | `/analytics` | `GET /analytics/*` |
| `DemandForecast.tsx` | `/forecast` | `POST /forecasts`, `GET /forecasts/{id}/explanation` |
| `ImportDemandData.tsx` | `/import` | `POST /ingest/*` |
| `Sources.tsx` | `/sources` | `GET /sources`, `/connectors`, `/api-keys` |
| `QualityMonitor.tsx` | `/quality` | `GET /quality/*` |
| `Alerts.tsx` | `/alerts` | `GET /alerts`, `POST /alerts/{id}/resolve` |
| `Reports.tsx` | `/reports` | `GET/POST /reports*` |
| `Settings.tsx` | `/settings` | `GET/PATCH /me/*`, `/members`, `/roles` |
| `Devices.tsx` | `/devices` | `GET/DELETE /me/sessions` — the full device list; rows mapped by `lib/sessions.ts`, shared with Profile's panel and the Settings Security tab |
| `DeviceFleet.tsx` | `/device-fleet` | `GET/POST/PATCH /devices`, `POST /devices/{id}/rotate\|quarantine\|restore\|revoke`, `GET /devices/{id}/effective-scopes\|grants\|events` — the restaurant's own hardware, mapped by `lib/deviceFleet.ts` and detailed in `FEATURES.md` |
| `Landing.tsx`, `Profile.tsx`, `Billing.tsx`, `ErrorPage.tsx` | `/`, `/profile`, `/billing`, `*` | `GET /auth/me`, marketing |
| `admin/` | `/admin/*` | `GET /models`, `/experiments`, `/features`, `/change-requests`, `/audit-log` (see `admin/README.md`) |
| `auth/` | `/auth/*` | `POST /auth/*` (see `auth/README.md`) |

## Key Props / Contracts / Data Sources
Key contracts per page listed in `FEATURES.md`. All pages read `forecasts.as_of_timestamp` + `model_version_id` (reproducibility, Ch 3.26) and respect `demand_observations UNIQUE(location_id,date)` on ingest.

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
