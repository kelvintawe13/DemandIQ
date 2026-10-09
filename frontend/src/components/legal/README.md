# frontend/src/components/legal

## Status
**Implemented prototype** — React/TypeScript source is present and runnable via `vite`.

## Purpose & Proposal Alignment
Legal/consent surfaces for Ch 3.28 data provenance (Recruit Kaggle competition conditions, URBAN-WASTE CC BY 4.0). Surfaces licences from `GET /datasets` + manifest and `licence` column on `waste_observations`.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| File | Role |
| --- | --- |
| `ConsentBanner.tsx` | Cookie/licence consent, dismiss persisted |
| `LegalModal.tsx` | Full licence + attribution text, links to Zenodo/Kaggle |

## Key Props / Contracts / Data Sources
Props: `ConsentBanner { onAccept, onDismiss }`; `LegalModal { open, dataset: { licence, source_dataset } }`. Data: `GET /datasets`, `GET /datasets/{id}/manifest`, `frontend/src/data/legal.ts`.

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
