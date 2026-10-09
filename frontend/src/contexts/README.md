# frontend/src/contexts

## Status
**Implemented prototype** — React/TypeScript source is present and runnable via `vite`.

## Purpose & Proposal Alignment
React contexts providing global state for session, navigation, theming, and the agent. Mirrors Ch 3.22 FastAPI → PostgreSQL stores (`workspaces`, `users`, `sessions`, `audit_log`) and the `GET /auth/me` + `GET /locations` bootstrap.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| File | Role |
| --- | --- |
| `SessionContext.tsx` | `user`, `workspace`, `permissions`, `refresh()` from `GET /auth/me` |
| `NavigationContext.tsx` | Active nav, breadcrumbs, `navItems` |
| `ThemeContext.tsx` | Theme/density (persisted via `GET/PATCH /me/preferences`) |
| `LocaleContext.tsx` | Language only (`en`/`fr`/`rw`); the workspace day/timezone lives in `lib/workspaceTime.ts` |
| `ToastContext.tsx` | Global toasts for ingest/forecast/policy feedback |
| `AgentContext.tsx` | Thread + context for AgentPanel |
| `LegalContext.tsx` | Consent + licence acknowledgement state |

## Key Props / Contracts / Data Sources
Contracts: `SessionContext` hydrates from `GET /auth/me`; `ThemeContext` syncs to `GET/PATCH /me/preferences`; the workspace day (`lib/workspaceTime.ts`, default `Africa/Kigali`) aligns with `workspaces.timezone` in `database/schema.sql:13`.

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
