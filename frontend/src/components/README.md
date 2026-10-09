# frontend/src/components

## Status
**Implemented prototype** — React/TypeScript source is present and runnable via `vite`.

## Purpose & Proposal Alignment
Reusable presentational and container components composing all pages. Supports Ch 3.22 user flow and Ch 3.23 UML diagrams (Use Case Fig 6, Activity Fig 7): header/chrome, ingestion tabs, forecast chart, analytics. Enforces the evidence boundary (Ch 3.21) by never co-rendering Recruit and URBAN-WASTE rows.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| Subfolder | Purpose |
| --- | --- |
| `agent/` | AI assistant overlay (AgentLauncher/Panel) |
| `analytics/` | Charts: DemandTrendChart, LocationDonut, Sparkline |
| `auth/` | AuthControls, AuthField, GoogleButton, TurnstileWidget |
| `chrome/` | HeaderActions, SyncButton, UserMenu |
| `forecast/` | ForecastChart (point + interval) |
| `illustrations/` | Landing/Page/Auth art, PosTerminal, SpreadsheetFile |
| `ingestion/` | ExcelCsvTab, ManualFormTab, PosMomoTab |
| `legal/` | ConsentBanner, LegalModal |
| `motion/` | Reveal animation wrapper |
| `overview/` | TodayPulseChart |
| `ui/` | Generic: Modal, Panel, Field, StatusPill, EmptyState, etc. |
| `AppShell.tsx`, `Sidebar.tsx`, `PageHeader.tsx`, `MobileNav.tsx`, etc. | Layout primitives |

## Key Props / Contracts / Data Sources
Props: most charts accept `{ data, range, height }`; ingestion tabs accept `{ uploadId, onCommit }`; chrome components consume `SessionContext` + `NavigationContext`. No component reads `database/schema.sql` directly; all data via API contracts.

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
