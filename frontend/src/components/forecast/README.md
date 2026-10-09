# frontend/src/components/forecast

## Status
**Implemented prototype** — React/TypeScript source is present and runnable via `vite`.

## Purpose & Proposal Alignment
Forecast visualisation for Ch 3.12-3.16 (point + adaptive conformal interval) and Ch 3.17-3.20 policy simulation. Renders `POST /forecasts` response `{ point, lower, upper, coverage_target, model_version_id }` and `GET /forecasts/{id}/explanation` SHAP impacts.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| File | Role |
| --- | --- |
| `ForecastChart.tsx` | Point line + interval band + realised overlay, date-brushed |
| `ForecastByDayChart.tsx` | One bar per forecast day with the 90% interval as an `ErrorBar` whisker — answers "which day is the big one". `ErrorBar` reads its array as *offsets* from the value, so the interval is converted before it is handed over |
| `DemandDistributionChart.tsx` | Histogram of demand values (`scope="forecast" | "history"`), bucketed by `lib/histogram`. Its y-axis counts **days**, so it uses `formatCountTick`, not the demand formatter |
| `WeekdayProfileChart.tsx` | Average demand per weekday from the imported history; needs history to mean anything, so it shows an empty state rather than seven zeroes |

Charts are fed only from the forecast rows and the ingested history already on the
page (`pages/DemandForecast.tsx`), so the gallery cannot disagree with the
breakdown table underneath it. Axis rules live in DESIGN.md § 16.

## Key Props / Contracts / Data Sources
Key props: `ForecastChart { point, lower, upper, realised?, targetDate, asOfTimestamp }`; interval label shows `coverage_target` (nominal 90%). Constraint: never renders `forecasts.as_of_timestamp` as editable; it is the reproducibility boundary (Ch 3.26 leakage prevention).

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
