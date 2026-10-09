# frontend/src/data

## Status
**Implemented (static content + types)** — imported by the live screens; the API is the data source everywhere else.

## Purpose & Proposal Alignment
Static modules that the UI genuinely imports: navigation/route metadata, auth seat + sign-up options,
legal copy, landing content, and shared types. Domain data (forecasts, policies, quality, alerts,
reports, webhooks, connectors, models, pipeline, users) is **not** mirrored here — those screens call
the live API (`frontend/src/api/client.ts`) and render `EmptyState` when a response is empty, per the
no-silent-blanks rule. Aligns with Ch 3.4-3.21 boundaries (Recruit vs URBAN-WASTE never merged).

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| File | Purpose | Key API |
| --- | --- | --- |
| `navItems.ts` | Nav sections + role gating (`sectionsForRole`, `itemsForRole`, `canAccess`) | — |
| `pages.ts` | Per-route page metadata/titles (`pageMeta`) | — |
| `demoAccount.ts` | Demo seats for the sign-in picker (`demoAccounts`, role → home screen) | `POST /auth/login` |
| `signupOptions.ts` | Business types + data-source choices on sign-up | `POST /auth/signup` |
| `legal.ts` | Terms/privacy documents for `LegalModal` + consent banner | `GET /legal/*` (copy is static) |
| `landing.ts` | Marketing copy, feature/step/evidence blocks | — |
| `agent.ts` | Assistant answer/insight types + opening prompts (no canned answers) | `POST /agent/messages` |
| `agentAdmin.ts` | Wording for assistant allowances + quota/session/guardrail types | `GET /admin/agent/*` |
| `forecast.ts` | Forecast point/interval type for the charts (no series) | `GET /forecasts` |

## Key Props / Contracts / Data Sources
These modules carry no domain fixtures: metric values, registry rows, policy trade-offs and SHAP
contributions all come from the API (e.g. `GET /models`, `GET /models/{id}/calibration`,
`GET /forecasts/{id}/explanation`). Any placeholder answer, series or insight that once lived here
was removed, because an unreferenced fabricated figure is one import away from being rendered.
Types here stay aligned with `shared/types` and `backend/app/schemas` (Pydantic).

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
