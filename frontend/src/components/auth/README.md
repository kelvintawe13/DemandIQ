# frontend/src/components/auth

## Status
**Implemented prototype** — React/TypeScript source is present and runnable via `vite`.

## Purpose & Proposal Alignment
Authentication UI for `POST /auth/*` and `GET /auth/me` (Ch 3.22 governance, Ch 3.28 ethical provenance). Implements register → verify-email → login → MFA flows, Turnstile, and Google OAuth, matching `API_ENDPOINTS.md` Auth section and `database/schema.sql` `users`, `sessions`, `mfa_recovery_codes`.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| File | Role |
| --- | --- |
| `AuthControls.tsx` | Shared submit/cancel + error mapping |
| `AuthField.tsx` | Label + input + validation message |
| `AuthLayout.tsx` | Centered card + illustration slot |
| `AuthSelect.tsx` | Role / business-type selectors for register |
| `BrandLogo.tsx` | DemandIQ wordmark |
| `GoogleButton.tsx` | OAuth entry, exchanges `id_token` at `POST /auth/oauth/google` |
| `TurnstileWidget.tsx` | Cloudflare Turnstile token for register/login |

## Key Props / Contracts / Data Sources
Key props: `AuthField { id, label, type, value, placeholder, icon, autoComplete, error, hint, onChange, trailing }`; `AuthSelect { id, label, value, options, placeholder, icon, error, hint, onChange }`; `TurnstileWidget { onVerify, onExpire, error }`; `GoogleButton { label, mode, onSuccess }`. Contracts: `POST /auth/register`, `/verify-email`, `/login`, `/mfa/verify`, `/oauth/google`, `/password/*`.

`AuthField` / `AuthSelect` follow the canonical forms spec in
[`DESIGN.md` § 9](../../../../DESIGN.md): a 13px label, `rounded-xl` chrome, a
`ring-2` at 20% on focus, and a 12px message that is `role="alert"` when it is an
error. Auth screens are light-only, so they carry no `dark:` variants. SignIn and
ResetPassword share these primitives.

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
