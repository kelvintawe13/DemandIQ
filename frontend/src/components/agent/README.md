# frontend/src/components/agent

## Status
**Implemented prototype** — React/TypeScript source is present and runnable via `vite`.

## Purpose & Proposal Alignment
Conversational assistant overlay tied to `AGENT_DESIGN.md` and `backend/AGENT_SERVICE.md` (spec-only backend). Aligns with Ch 3.24 explainability: agent surfaces SHAP attributions and forecast narratives in natural language without claiming causality. Frontend-only mock; future backend will expose `POST /agent/chat`.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| File | Role |
| --- | --- |
| `AgentLauncher.tsx` | Floating button + unread badge, toggles panel |
| `AgentPanel.tsx` | Chat history, tool-call chips, markdown rendering |

## Key Props / Contracts / Data Sources
Key props: `AgentPanel { context: { locationId, targetDate, forecastId }, threadId }`; consumes `AgentContext`. Data source: `frontend/src/data/agent.ts` + `agentAdmin.ts` mocks; future contract `POST /agent/chat -> { message, toolCalls, sources }`.

DB invariants referenced: `demand_observations UNIQUE(location_id,date)` (`database/schema.sql:132`), `forecasts.as_of_timestamp` NOT NULL (`schema.sql:227`), evidence boundary `waste_observations` with no FK (`schema.sql:268-271`), two-person rule `CHECK(decided_by<>requested_by)` (`schema.sql:405`).

ML contracts: `predict(location_id, target_date, as_of) -> Forecast { point, lower, upper, coverage_target, model_version_id }` and `simulate(...) -> PolicyResult { surplus_sr, surplus_ps, unmet_ud }` per `model/README.md`.

## Related Docs
- [API Contract](../../../../backend/API_ENDPOINTS.md)
- [Architecture](../../../../backend/ARCHITECTURE.md)
- [DB Schema](../../../../database/schema.sql)
- [ML Contracts](../../../../model/README.md)
- [Feature Spec](../../../../model/FEATURE_SPEC.md)
- [Proposal](../../../../project proposal.md)
- [API Console](../../backend/api-console.html) (relative; open in browser, mock mode)
