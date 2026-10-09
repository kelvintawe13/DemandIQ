# frontend

## Status

**Fully Real** — React/TypeScript live — 333× `EmptyState`, live `frontend/src/api/client.ts:1`
(`VITE_API_URL`, `Bearer`, `EmptyState` mapping), 189 ts+tsx (153 in `src/`, 36 in `__tests__/`)
(`backend/app/main.py:135` 177 paths → 213 items).

## Purpose & Proposal Alignment

React 18 + Vite + Tailwind web application. Implements the Operator surfaces in Fig 5 (Proposal Ch
3.22): Web app → FastAPI (`backend/app/main.py:135`) → Forecasting/Policy/Explainability →
PostgreSQL (`database/schema.sql` 530 lines). Covers Ch 3.4-3.11 features (ingestion, forecasting,
policy comparison) and the end-to-end user flow: select restaurant → select forecast date → generate
forecast → view interval (Ch 3.16) → compare policies SR/PS/UD (Ch 3.17-3.20) → inspect explanation
(Ch 3.24). Every empty/error/offline state renders `EmptyState` (333×).

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE),
**3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21**
URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5
(React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map

| File                                       | Role                                                                                                                        |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| `index.html`                               | Vite entry HTML                                                                                                             |
| `vite.config.ts`                           | Dev server + build config (8.9s)                                                                                            |
| `tailwind.config.js` / `postcss.config.js` | Styling pipeline                                                                                                            |
| `src/api/client.ts:1`                      | **Live** typed API client — `BASE = VITE_API_URL` (`docker-compose.yml:73`), `Bearer`, `x-request-id`, `EmptyState` helpers |
| `src/components/ui/EmptyState.tsx`         | Empty/error/offline/search — 333× usage                                                                                     |
| `src/`                                     | Application source (see `src/README.md`) — 124 ts+tsx, `src/data/` is types/config not primary                              |
| `src/data/`                                | Types + config only (canonical data is `database/schema.sql` + API)                                                         |
| `dist/`                                    | Build output (generated) — built with `base: '/app/'`, because `backend/app/main.py` mounts it at `/app`                    |
| `Dockerfile.dev`                           | Dev container                                                                                                               |

API surface: `GET/POST /forecasts`, `/policies/simulate`, `/forecasts/{id}/explanation`,
`/ingest/*`, `/quality/*`, `/alerts`, `/analytics/*` per `backend/API_ENDPOINTS.md` (177 paths →
213 items, `backend/scripts/sync_console.py`). No direct DB/ML access. Shared types from `shared/`.

## Key Props / Contracts / Data Sources

Contracts: consumes `GET/POST /forecasts`, `/policies/simulate`, `/forecasts/{id}/explanation`,
`/ingest/*`, `/quality/*`, `/alerts`, `/analytics/*` per `backend/API_ENDPOINTS.md` (177 paths →
213 items). Live client is `frontend/src/api/client.ts:1`; server is `backend/app/main.py:135`. No
direct DB/ML access. Shared types from `shared/`.

DB invariants: `demand_observations UNIQUE(location_id,date)` (`database/schema.sql:132`),
`forecasts.as_of_timestamp` NOT NULL (`schema.sql:227`), evidence boundary `waste_observations`
with no FK (`schema.sql:277`), two-person rule `CHECK(decided_by<>requested_by)` (`schema.sql:405`),
49 tables (`database/schema.sql` + `database/AGENT_SCHEMA.sql`).

ML contracts: `predict(location_id, target_date, as_of) -> Forecast { point, lower, upper,
coverage_target, model_version_id }` and `simulate(...) -> PolicyResult { surplus_sr, surplus_ps,
unmet_ud }` per `model/README.md`. All empty states are `EmptyState` (333×).

## Related Docs

- [API Contract](../backend/API_ENDPOINTS.md) — 177 paths → 213 items (`backend/scripts/sync_console.py`)
- [Architecture](../backend/ARCHITECTURE.md)
- [DB Schema](../database/schema.sql) — 530 lines
- [ML Contracts](../model/README.md)
- [Feature Spec](../model/FEATURE_SPEC.md)
- [Proposal](../project\ proposal.md)
- [API Console](../backend/api-console.html) — live 213 items
- [Client](./src/api/client.ts) — `frontend/src/api/client.ts:1`
