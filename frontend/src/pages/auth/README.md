# frontend/src/pages/auth

## Status
**Implemented prototype** — React/TypeScript source is present and runnable via `vite`.

## Purpose & Proposal Alignment
Authentication flow pages for `backend/API_ENDPOINTS.md` § Auth and `database/schema.sql` `users`/`sessions`/`mfa_recovery_codes`. Implements register → verify → login → MFA → recovery, plus OAuth and password reset, with Turnstile and rate-limit handling.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics (MAE/RMSE/RMSLE), **3.16** uncertainty (adaptive conformal 90%), **3.17-3.20** policy simulation SR/PS/UD, **3.21** URBAN-WASTE evidence boundary (Fig 4), **3.22** architecture Fig 5 (React → FastAPI → Forecasting/Policy/Explainability → PostgreSQL).

## File Map
| File | Route | Contract |
| --- | --- | --- |
| `SignUp.tsx` | `/auth/signup` | `POST /auth/register` (+ `turnstile_token`) |
| `VerifyEmail.tsx` / `CheckEmail.tsx` / `AllSet.tsx` | `/auth/verify*` | `POST /auth/verify-email`, `/resend-verification` |
| `SignIn.tsx` / `SigningIn.tsx` | `/auth/signin` | `POST /auth/login` → `{tokens}` or `{mfa_required, challenge_id}` |
| `TwoFactor.tsx` | `/auth/mfa` | `POST /auth/mfa/verify`, `POST /me/mfa/*` |
| `ResetPassword.tsx` | `/auth/reset` | `POST /auth/password/forgot` (202), `POST /auth/password/reset` |

## Key Props / Contracts / Data Sources
Key props: forms validate `email` (CITEXT UNIQUE), `password` strength, `code` 6-digit. On success stores `access_token`/`refresh_token` and hydrates `SessionContext` via `GET /auth/me`.

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
