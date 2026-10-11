# DemandIQ — API Contract

Base URL: `/api/v1` — **177 paths → 213 items** (`openapi.json` via `backend/app/main.py:135`, `backend/scripts/sync_console.py`). All routes require `Authorization: Bearer <jwt>` unless marked public. Routes marked **admin** require the Administrator role. Live catalog is `api-console.html` (213 items, sync via `backend/scripts/sync_console.py`); frontend client is `frontend/src/api/client.ts:1`.

---

## Auth

| Method | Path                        | Description                                                                                                                                                                                                                          |
| ------ | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `POST` | `/auth/register`            | Public. Creates an account and a workspace. Body: `name`, `email`, `password`, `business_name`, `business_type`, `location_count`, `daily_covers`, `data_sources[]`, `turnstile_token`; optional `role` (capped by the platform `default_role` — new sign-ups are **owners** by default, so an organisation joining the platform can manage its own workspace). Returns a pending-verification user. |
| `POST` | `/auth/verify-email`        | Public. Body: `email`, `code` (6 digits). Activates the account.                                                                                                                                                                     |
| `POST` | `/auth/resend-verification` | Public. Re-issues the email code. Rate limited.                                                                                                                                                                                      |
| `POST` | `/auth/login`               | Public. Body: `email`, `password`, `turnstile_token`. Returns either `{ access_token, refresh_token, user }` or `{ mfa_required: true, challenge_id }`.                                                                              |
| `POST` | `/auth/mfa/verify`          | Public. Body: `challenge_id`, `code` or `recovery_code`, `trust_device`. Returns tokens.                                                                                                                                             |
| `POST` | `/auth/oauth/google`        | Public. Body: `id_token`. Creates or links the account, may return `mfa_required`.                                                                                                                                                   |
| `POST` | `/auth/password/forgot`     | Public. Body: `email`. Always 202, never reveals whether the address exists.                                                                                                                                                         |
| `POST` | `/auth/password/reset`      | Public. Body: `token`, `password`.                                                                                                                                                                                                   |
| `POST` | `/auth/refresh`             | Exchanges a refresh token for a new access token.                                                                                                                                                                                    |
| `POST` | `/auth/logout`              | Revokes the current refresh token.                                                                                                                                                                                                   |
| `GET`  | `/auth/me`                  | Current user, role, workspace and permissions.                                                                                                                                                                                       |

## Account & security

| Method   | Path                        | Description                                                      |
| -------- | --------------------------- | ---------------------------------------------------------------- |
| `PATCH`  | `/me/profile`               | Update name, email, phone, job title, avatar URL.                |
| `POST`   | `/me/avatar`                | Multipart upload, returns the stored URL.                        |
| `POST`   | `/me/password`              | Body: `current_password`, `new_password`.                        |
| `GET`    | `/me/preferences`           | Theme, language, density, default location.                      |
| `PATCH`  | `/me/preferences`           | Update the above.                                                |
| `GET`    | `/me/notifications`         | Per-channel notification settings.                               |
| `PATCH`  | `/me/notifications`         | Update them.                                                     |
| `POST`   | `/me/mfa/enroll`            | Returns a TOTP secret and provisioning URI.                      |
| `POST`   | `/me/mfa/confirm`           | Body: `code`. Enables 2FA, returns recovery codes once.          |
| `POST`   | `/me/mfa/recovery-codes`    | Regenerates recovery codes, invalidating the old set.            |
| `DELETE` | `/me/mfa`                   | Disables 2FA. Rejected for Administrator seats.                  |
| `GET`    | `/me/sessions`              | Active sessions with device, recorded `origin` (the client address as it stood at sign-in — a country code only where the deployment resolves one) and last-seen; `current: true` on the one the request's token belongs to (`sid` claim). |
| `DELETE` | `/me/sessions/{id}`         | Revoke one session.                                              |
| `DELETE` | `/me/identities/{provider}` | Unlink an OAuth provider. Rejected if it is the only credential. |
| `DELETE` | `/me/account`                 | Permanently erase the caller's own account. Body: `confirm: "DELETE"` (literal) and `current_password` (required only when the account has a password; OAuth-only accounts omit it). `409 last_owner` when the caller is the only owner of a workspace that still has other members. A workspace is removed only when the caller was its last member. `audit_log.actor_id` is nulled rather than deleted, so the audit trail survives the erasure. |

## Demand ingestion

| Method   | Path                               | Description                                                                                                                        |
| -------- | ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `POST`   | `/ingest/manual`                   | Body: array of `{ location_id, date, demand_qty, notes }`. Validated and upserted. `demand_qty` is `0`–`10,000,000`; anything larger is refused per row with `422 invalid_demand` (`normaliser.MAX_DEMAND_QTY`). |
| `POST`   | `/ingest/file`                     | Multipart CSV or XLSX. Returns `upload_id`, detected headers and the first 5 parsed rows for preview.                              |
| `POST`   | `/ingest/file/{upload_id}/mapping` | Body: `{ column_map: { date, demand_qty, location } }`. Validates the mapping and returns a dry-run row count plus any row errors. |
| `POST`   | `/ingest/file/{upload_id}/commit`  | Commits the mapped rows into `demand_observations`.                                                                                |
| `POST`   | `/ingest/image`                    | Multipart image of a handwritten log. Queues OCR, returns `job_id`.                                                                |
| `GET`    | `/ingest/image/{job_id}`           | OCR status and extracted rows for review. Each row carries `confidence`; `engine` (`remote` \| `tesseract` \| `none`) says which engine read it, because a vision service returns a model score while the local parser's value counts how many numbers were left on the line — a readability signal, not a probability. |
| `POST`   | `/ingest/image/{job_id}/commit`    | Commits corrected OCR rows.                                                                                                        |
| `GET`    | `/ingest/uploads`                  | Recent uploads with status, row counts and errors.                                                                                 |
| `GET`    | `/ingest/demand`                   | Stored demand rows, newest first. Filters: `location_id`, `upload_id` (the rows one import wrote), `limit` ≤ 50, `offset`. Each row reports `source` and its provenance — `upload_id` / `upload_filename`, `null` for a row that never came from a file. |
| `DELETE` | `/ingest/uploads/{id}`             | Discards an uncommitted upload.                                                                                                    |

## Locations & menu

| Method                  | Path                  | Description                                                             |
| ----------------------- | --------------------- | ----------------------------------------------------------------------- |
| `GET`                   | `/locations`          | List with history depth, average covers, connector and forecastability. |
| `POST`                  | `/locations`          | **admin** Create. Body: `name`, `area`, `cuisine`, `connector_id`.      |
| `GET`                   | `/locations/{id}`     | Detail including ingestion history and data gaps.                       |
| `PATCH`                 | `/locations/{id}`     | **admin** Update.                                                       |
| `DELETE`                | `/locations/{id}`     | **admin** Archive (never hard delete; history is retained).             |
| `GET`                   | `/menu-groups`        | Groups with item counts, share of covers and perishability.             |
| `POST` `PATCH` `DELETE` | `/menu-groups[/{id}]` | **admin** Manage groups.                                                |

## Forecasting

| Method | Path                                      | Description                                                                                                                                  |
| ------ | ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST` | `/forecasts`                              | Body: `location_id`, `target_date`, `horizon_days`, `granularity`. Returns `point`, `lower`, `upper`, `coverage_target`, `model_version_id`. |
| `GET`  | `/forecasts`                              | Query by `location_id`, `from`, `to`. Historical forecasts with realized demand once known.                                                  |
| `GET`  | `/forecasts/{id}`                         | One forecast with its interval, policy outcome and realized demand.                                                                          |
| `GET`  | `/forecasts/{id}/explanation`             | SHAP contributions: `feature`, `value`, `impact` in visitor units.                                                                           |
| `GET`  | `/forecasts/{id}/explanation/summary`     | The same attribution as a plain-language summary: `summary`, `dominant_factor`, `positive_factors`, `negative_factors`, `total_drivers`, `total_impact_visitors`, `residual_visitors`. Visitor units; predictive, not causal. |
| `GET`  | `/forecasts/{id}/policy/simulation`       | The four preparation policies simulated on this forecast's day: per-policy `prepared_qty`, `surplus_sr`, `surplus_ps`, `unmet_ud`, plus `lowest_unmet_demand` / `lowest_realised_surplus`. A rule with nothing to measure returns `null` with `unavailable_reason`, never a stand-in number. |
| `GET`  | `/forecasts/{id}/drift/monitoring`        | Site-level demand drift: PSI between the location's last 30 observed days and its prior 90-day baseline, with `drift_status` (<0.10 `no_change`, 0.10-0.25 `moderate_change`, >=0.25 `significant_change`) and a `recommendation`. |
| `GET`  | `/forecasts/{id}/export?format=csv\|xlsx` | File download of the forecast window.                                                                                                        |
| `POST` | `/forecasts/batch`                        | Nightly job hook; forecasts every forecastable location.                                                                                     |

## Preparation policies

| Method  | Path                 | Description                                                                                                                            |
| ------- | -------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`   | `/policies`          | The three configured policies and their parameters.                                                                                    |
| `POST`  | `/policies/simulate` | Body: `location_id`, `period`, `policies[]`, `quantile`. Returns per-policy `surplus_sr`, `surplus_ps`, `unmet_ud`, event frequencies. Realised figures (`surplus_ps`, `unmet_ud`) are `null` until the day has an observed outcome; `prepared_qty` (and everything derived from it) is `null` with `unavailable_reason: "no_history"` when the rule has nothing to measure — never a stand-in number. |
| `PATCH` | `/policies/{id}`     | **admin** Change the active quantile or rule. Creates a change request rather than applying immediately.                               |

## Analytics & reporting

| Method                  | Path                        | Description                                                                     |
| ----------------------- | --------------------------- | ------------------------------------------------------------------------------- |
| `GET`                   | `/analytics/summary`        | KPIs for a date range and location, with previous-period comparison.            |
| `GET`                   | `/analytics/trend`          | Daily or weekly demand series, current vs previous period.                      |
| `GET`                   | `/analytics/by-location`    | Demand share and rank per location.                                             |
| `GET`                   | `/analytics/breakdown`      | Per-location table rows with sparkline series.                                  |
| `GET`                   | `/analytics/insights`       | Generated observations (peaks, drops, coverage misses).                         |
| `GET`                   | `/reports/templates`        | Available report templates.                                                     |
| `POST`                  | `/reports/generate`         | Body: `template_id`, `period`, `format`, `location_ids[]`. Returns `report_id`. |
| `GET`                   | `/reports/{id}`             | Status and, when ready, a signed download URL.                                  |
| `GET`                   | `/reports`                  | Recently generated reports.                                                     |
| `GET`                   | `/reports/schedules`        | Scheduled deliveries.                                                           |
| `POST` `PATCH` `DELETE` | `/reports/schedules[/{id}]` | Manage schedules (cadence, recipients, format, enabled).                        |

## Data quality & alerts

| Method        | Path                                     | Description                                                                               |
| ------------- | ---------------------------------------- | ----------------------------------------------------------------------------------------- |
| `GET`         | `/quality/score`                         | Overall score and per-dimension scores (completeness, timeliness, consistency, accuracy). |
| `GET`         | `/quality/issues`                        | Open issues with suggested fixes.                                                         |
| `POST`        | `/quality/issues/{id}/apply-fix`         | Applies the suggested correction.                                                         |
| `POST`        | `/quality/issues/{id}/dismiss`           | Dismisses with a reason.                                                                  |
| `GET` `PATCH` | `/quality/rules[/{id}]`                  | Validation rules and their enabled state.                                                 |
| `GET`         | `/alerts`                                | Filter by `status` (unread, critical, resolved).                                          |
| `POST`        | `/alerts/{id}/read` · `/alerts/read-all` | Mark read.                                                                                |
| `POST`        | `/alerts/{id}/resolve`                   | Resolve with an optional note.                                                            |
| `GET` `PATCH` | `/alerts/rules[/{id}]`                   | Alert rules and thresholds.                                                               |

## Sources & connectors

| Method           | Path                                              | Description                                                              |
| ---------------- | ------------------------------------------------- | ------------------------------------------------------------------------ |
| `GET`            | `/sources`                                        | Channels with status, freshness, volume, and each channel's own `last_pull` (`pulled`/`committed`/`skipped`/`detail`, or `null` counts before a pull has run). |
| `POST`           | `/sources/{id}/sync`                              | Queue a pull for an **active** channel, returns `job_id` (the run stays `queued` until drained). A non-active connector is refused with `409 not_active` — the job only selects `active`, so a queued run for anything else would settle as `success` having pulled nothing. |
| `GET`            | `/sources/sync-history`                           | Recent sync runs.                                                        |
| `GET`            | `/connectors`                                     | **admin** Connector definitions: type, name, status, health.             |
| `POST`           | `/connectors`                                     | **admin** Create. Body: `type`, `name`, `location_ids[]`, `credentials`. |
| `PATCH` `DELETE` | `/connectors/{id}`                                | **admin** Update or remove.                                              |
| `POST`           | `/connectors/{id}/test`                           | **admin** Precondition check, returns latency and the canonical sample payload. |
| `POST`           | `/connectors/{id}/pull`                           | **admin** Pull this one connector now and commit the rows; records the outcome as its `last_pull` and returns `run_id` (a real pull also writes a `connector_sync` run, so it appears in `GET /pipeline/runs` and the Sources history). `?dry_run=true` maps and validates but writes nothing — no rows, no run (`run_id: null`) — returning the rows that would commit. Requires a configured endpoint (`409 not_configured`); a real pull requires an `active` channel (`409 not_active`), while a dry run is allowed on any status. A provider failure is `502 provider_unavailable`. |
| `GET` `PATCH`    | `/connectors/{id}/mapping`                        | **admin** Field mapping to the canonical schema.                         |
| `GET`            | `/api-keys`                                       | **admin** Keys with scope, prefix and last use.                          |
| `POST`           | `/api-keys`                                       | **admin** Create; the full secret is returned exactly once.              |
| `POST`           | `/api-keys/{id}/rotate` · `DELETE /api-keys/{id}` | **admin** Rotate or revoke.                                              |

## Models, experiments & features — all **admin**

| Method       | Path                        | Description                                                                        |
| ------------ | --------------------------- | ---------------------------------------------------------------------------------- |
| `GET`        | `/models`                   | Registry rows: algorithm, MAE, RMSE, RMSLE, status.                                |
| `GET`        | `/models/serving`           | **Live** What forecasts are answered with now, beside the row marked production. `configuration` reports the serving mode (`trained`/`stub`/`error`) and whether `MODEL_BUNDLE_PATH` named the export or it was discovered. |
| `GET`        | `/models/{id}`              | Detail with hyperparameters, training window and calibration.                      |
| `POST`       | `/models/train`             | Queues a training run. Body: `algorithm`, `feature_set`, `params`.                 |
| `POST`       | `/models/{id}/promote`      | Raises a promotion change request (two-person rule).                               |
| `POST`       | `/models/{id}/rollback`     | Restores the previous production version.                                          |
| `GET`        | `/models/{id}/calibration`  | Target vs empirical coverage, mean interval width.                                 |
| `GET`        | `/models/{id}/importance`   | Aggregate feature importance.                                                      |
| `GET` `POST` | `/experiments`              | Backtests. `POST` body: `name`, `hypothesis`, `algorithm`, `feature_set`, `folds`. |
| `GET`        | `/experiments/{id}`         | Fold-level MAE, coverage, baseline delta.                                          |
| `POST`       | `/experiments/{id}/submit`  | Sends the winning config to the approvals queue.                                   |
| `GET`        | `/features`                 | Feature definitions with coverage and leakage status.                              |
| `GET`        | `/features/{name}/coverage` | Per-location coverage for one feature.                                             |

## Governance — all **admin**

| Method       | Path                                        | Description                                                               |
| ------------ | ------------------------------------------- | ------------------------------------------------------------------------- |
| `GET`        | `/datasets`                                 | Registered datasets, record counts, licence, access conditions.           |
| `GET`        | `/datasets/{id}/manifest`                   | Reproducibility manifest (checksums, date range, preprocessing version).  |
| `GET`        | `/waste/summary`                            | URBAN-WASTE descriptive statistics plus `establishment_variation` (per-kitchen count, mean, sd, min/max — bounded to the 10 busiest, and `establishments` gives the total). Never joined to demand. |
| `GET` `POST` | `/change-requests`                          | Pending promotions, policy changes, access grants with before/after diff. |
| `POST`       | `/change-requests/{id}/approve` · `/reject` | Decide. The requester may not approve their own request.                  |
| `GET`        | `/members` · `POST /members/invite`         | Roster and invitations. A role must rank *below* the caller's own and can never be `owner`. |
| `PATCH`      | `/members/{id}`                             | Change role or location scope. Refused for your own role (that is self-escalation) and for any account at or above your rank. |
| `DELETE`     | `/members/{id}`                             | Revoke access. Likewise only for accounts below your rank, so the owner cannot be removed by anyone. |
| `GET`        | `/roles`                                    | Role definitions, the permission matrix, and each role's `rank`/`assignable` position in the hierarchy. |
| `GET`        | `/audit-log`                                | Filterable by actor, action, client and date.                             |
| `GET`        | `/pipeline/runs`                            | Ingestion, training, calibration and forecasting job runs.                |
| `POST`       | `/pipeline/runs/{id}/rerun`                 | Re-runs a failed job.                                                     |
| `GET`        | `/pipeline/drift`                           | Live vs training distribution signals.                                    |
| `GET`        | `/pipeline/schedule`                        | Cadence per job type plus its `last_run` (newest `pipeline_runs` row) and computed `next_run`. |
| `POST`       | `/pipeline/jobs/{job_type}/run`             | Run one scheduled job on demand for this workspace.                       |
| `POST`       | `/pipeline/queue/drain`                     | Complete this workspace's `queued` runs now (and reap abandoned ones) without waiting for a worker tick. |

### Background jobs

One tick — reap `running` rows whose process died, drain the `queued` rows, then run the jobs whose cron
matches — is defined once in `app/jobs/scheduler.py:run_tick` and driven either in-process (`JOBS_ENABLED=true`,
started from the app lifespan) or by the standalone worker `python -m app.jobs.worker` (`--once` for a
CronJob, `--tick-seconds` to override). A failed run raises one open `Pipeline job failed: <job_type>` alert
and fans out `alert.raised`; the next success resolves it and emits `alert.resolved`.

`connector_sync` performs a real pull: for each `active` connector it GETs the provider
endpoint in the connector's credentials (with `?since=<last_sync_at>` for an incremental
pull), maps the returned rows through the connector's field mapping, and commits them via
`commit_demand`, tagged with the connector's `source_id`. Rows that cannot be mapped are
counted as skipped, never guessed at; a connector with no endpoint is reported as such and
does not get a `last_sync_at` stamp.

## Webhooks — **admin**

| Method           | Path                        | Description                                    |
| ---------------- | --------------------------- | ---------------------------------------------- |
| `GET` `POST`     | `/webhooks`                 | Endpoints and their subscribed events.         |
| `PATCH` `DELETE` | `/webhooks/{id}`            | Update URL, events or status.                  |
| `POST`           | `/webhooks/{id}/test`       | Sends a signed sample payload.                 |
| `GET`            | `/webhooks/{id}/deliveries` | Delivery log with response codes and attempts. |
| `GET`            | `/webhooks/events`          | Catalog of emittable events.                   |

**Outbound events:** `forecast.generated`, `forecast.updated`, `policy.changed`,
`alert.raised`, `alert.resolved`, `ingest.completed`, `ingest.failed`,
`model.promoted`. Payloads are signed with `X-DemandIQ-Signature`
(HMAC-SHA256 over the raw body), retried 5 times with exponential backoff.

## Agent & Billing

Live — `backend/app/routers/agent.py:28` + `backend/app/routers/billing.py:34`, 15 agent + 10 billing
paths (see `AGENT_SERVICE.md:24`). Agent is read-only orchestration; billing is workspace-scoped.

### Agent — read-only assistant (`/agent/*` + `/admin/agent/*`) — 15 paths

| Method        | Path                            | Description                                                    |
| ------------- | ------------------------------- | -------------------------------------------------------------- |
| `POST`        | `/agent/sessions`               | Open a session; returns id, limits, remaining quota            |
| `GET`         | `/agent/sessions`               | List the caller's sessions (admins: workspace-wide)            |
| `DELETE`      | `/agent/sessions/{id}`          | End a session; discards its context                            |
| `POST`        | `/agent/messages`               | Ask a question; streams the answer                             |
| `GET`         | `/agent/messages?session_id=`   | Replay a transcript                                            |
| `POST`        | `/agent/messages/{id}/pin`      | Save an answer to the caller's workspace                       |
| `GET`         | `/agent/insights`               | Proactive cards for the caller's scope                         |
| `GET`         | `/agent/quota`                  | Remaining allowance for the caller                             |
| `GET`         | `/admin/agent/quotas`           | **admin** Seat allowances and consumption — one row per member, with the tier, both usage counters and any `daily_override` |
| `PATCH`       | `/admin/agent/quotas/{user_id}` | **admin** Change tier or set a daily override                  |
| `POST`        | `/admin/agent/grants`           | **admin** Issue a temporary top-up with an expiry              |
| `GET`         | `/admin/agent/sessions`         | **admin** All live sessions in the workspace                   |
| `DELETE`      | `/admin/agent/sessions/{id}`    | **admin** Force-end any session                                |
| `GET`/`PATCH` | `/admin/agent/guardrails`       | **admin** Read and toggle unlockable rules                     |
| `GET`         | `/admin/agent/usage`            | **admin** Daily question volume, tool calls, refusals, latency |

Quota is counted in **questions**, not tokens; checked before the planner runs (`429
quota_exhausted_daily/monthly`, `429 rate_limited` with `Retry-After`).

### Billing — workspace subscription (`/billing/*`) — 10 paths

| Method       | Path                              | Description                             |
| ------------ | --------------------------------- | --------------------------------------- |
| `GET`        | `/billing/status`                 | Current subscription status + trial     |
| `GET`        | `/billing/plans`                  | Static plans (Starter / Growth / Scale) |
| `GET` `POST` | `/billing/subscription`           | Get or switch plan (owner/admin)        |
| `GET`        | `/billing/usage`                  | Live usage from locations + forecasts   |
| `GET`        | `/billing/invoices`               | List invoices for the workspace         |
| `GET`        | `/billing/invoices/{id}/download` | Download one invoice                    |
| `GET` `POST` | `/billing/payment-method`         | Get or update last4/brand               |
| `GET` `POST` | `/billing/contact`                | Get or update billing contact           |
| `POST`       | `/billing/cancel`                 | Cancel at period end                    |
| `POST`       | `/billing/resume`                 | Resume a cancelled subscription         |

Plans are static config (`backend/app/routers/billing.py:27` `PLANS`); subscription is per-workspace;
invoices are per billing period; usage is computed live. Backend is `backend/app/main.py:276`
(`include_router(billing.router)`); frontend billing UI maps to these paths.

## Site administration — **platform flag**

Gated on `users.is_site_admin`, not on a workspace role: a workspace `owner` manages
their own workspace, this console manages the platform. Directory writes enforce the
same hierarchy as member management (`app/security/permissions.py`): you may only act
on accounts strictly below your own rank, may only grant roles below it, never
`owner`, and site-administrator accounts are not demoted or deactivated from here.

| Method       | Path                          | Description                                                                              |
| ------------ | ----------------------------- | ---------------------------------------------------------------------------------------- |
| `GET`        | `/admin/site/overview`        | Platform stats: users (total/active), workspaces, locations, forecasts, models, seats by role. |
| `GET`        | `/admin/site/health`          | Runtime facts: app version/env, DB status + latency, uptime, Alembic revision, live sessions, last forecast/training. |
| `GET`        | `/admin/site/users`           | Cross-workspace directory. `q`, `role` and `status` (`active\|unverified\|deactivated`) filters, `limit`/`offset`, `has_more`. |
| `GET`        | `/admin/site/users/{id}`      | One account in full: workspace, live sessions (device/country/last-seen), MFA, verified state, linked identities, its own audit trail. |
| `PATCH`      | `/admin/site/users/{id}`      | Change role / deactivate / reactivate / rename. Hierarchy-checked; refusal messages name the reason. |
| `POST`       | `/admin/site/users/{id}/reset-password` | Set a password directly and revoke that account's sessions.                    |
| `GET`        | `/admin/site/workspaces`      | Every tenant with user/location/forecast counts.                                         |
| `GET`        | `/admin/site/workspaces/{id}` | One tenant's footprint, forecast window and members.                                     |
| `PATCH`      | `/admin/site/workspaces/{id}` | Rename or change business type / country / timezone (audited).                            |
| `GET`        | `/admin/site/settings`        | Platform settings, defaults merged, `is_default` flagged.                                 |
| `PATCH`      | `/admin/site/settings`        | Upsert settings (unknown keys accepted for forward compatibility).                        |
| `GET`        | `/admin/site/audit`           | Privileged actions. `q` (action or actor), `action` prefix, `client` filters, `limit`/`offset`, `has_more`. |
| `GET`        | `/admin/site/meta`            | **Public** subset clients react to: `registration_open`, `announcement`, `support_email`, `maintenance_mode`. |

## Device fleet, grants & remote support — 19 paths

Customer hardware (tills, KDS screens, sensors) enrolled against a workspace. Every route below
but the heartbeat and the demand write take a JWT and a capability. Those two are the exceptions:
they authenticate with the device's own credential in `X-Device-Key`, because a device reporting
its own liveness or its own numbers is not a person and must never be able to stand in for one.

### Fleet (`/devices/*`) — needs `devices:read` / `devices:manage`

Most routes here take a JWT and an operator capability. `/heartbeat` and `/demand` do not: they
take the device credential. The split is at the route rather than inside a dependency, because a
dependency that accepts "a session or a device key" is a dependency that eventually lets a till
act as an employee.

| Method   | Path                        | Description                                                                                                                                     |
| -------- | --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`    | `/devices`                  | List the fleet, filterable by `status`/`location_id`                                                                                           |
| `POST`   | `/devices`                  | Enrol. Returns the credential **once** — only the bcrypt hash and a non-secret prefix are stored, so there is no path that could re-show it |
| `GET`    | `/devices/{device_id}`      | One device                                                                                                                                    |
| `PATCH`  | `/devices/{device_id}`      | Update label/location/scopes. A cross-workspace id is **404, not 403** — 403 would confirm the row exists                    |
| `GET`    | `/devices/{device_id}/events` | Device telemetry, newest first                                                                                                                 |
| `POST`   | `/devices/{device_id}/heartbeat` | **Device-authenticated** (`X-Device-Key`). Liveness + allowlisted telemetry; a path naming another device is 404        |
| `POST`   | `/devices/{device_id}/demand`    | **Device-authenticated** (`X-Device-Key`). Persist reported demand. This is the durable half of the WebSocket round trip, and it is the only device path that writes forecast data |
| `POST`   | `/devices/{device_id}/rotate`   | Issue a new credential, invalidating the old one                                                                                            |
| `POST`   | `/devices/{device_id}/revoke`    | Revoke the credential                                                                                                                        |
| `POST`   | `/devices/{device_id}/quarantine` | Quarantine without revoking, so the key survives a false positive                                                                             |
| `POST`   | `/devices/{device_id}/restore`   | Lift a quarantine                                                                                                                             |

### Grants (`/device-grants/*`) — needs `device_grants:manage`

| Method   | Path                                                    | Description                                                                                                      |
| -------- | ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `GET`    | `/device-grants/devices/{device_id}/grants`             | Every grant on the device, live and revoked                                                                      |
| `POST`   | `/device-grants/devices/{device_id}/grants`             | Grant a capability. Responds with `effective_scopes` — the device's scopes *after* the change, against the server clock |
| `DELETE` | `/device-grants/devices/{device_id}/grants/{grant_id}` | Revoke. The row survives as the record of the withdrawal; `revoked_at`/`revoked_by` stay populated                  |
| `GET`    | `/device-grants/devices/{device_id}/effective-scopes`  | Base scopes, per-grant attributions, and the effective and withheld sets                                         |

A grant may only *widen* what the device was enrolled with, and the effective set is base scopes
plus non-expired, non-revoked grants. The boundary is evaluated server-side only — the console
never re-derives it, because a second implementation of the expiry rule is a second answer to
"is this permission on right now".

### Remote support (`/device-mirror/*`) — request ≠ approve

| Method   | Path                                                     | Description                                                                          |
| -------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| `GET`    | `/device-mirror/devices/{device_id}/mirror-eligibility`   | Whether the device can be mirrored and, if not, which gate is unmet                  |
| `POST`   | `/device-mirror/devices/{device_id}/mirror-consent`       | Record or withdraw customer consent                                                  |
| `POST`   | `/device-mirror/devices/{device_id}/mirror-requests`      | Request a session — needs `device_mirror:request`                                     |
| `GET`    | `/device-mirror/devices/{device_id}/mirror-requests`      | A device's session history                                                          |
| `GET`    | `/device-mirror/mirror-requests/pending`                  | The approval queue                                                                  |
| `POST`   | `/device-mirror/mirror-requests/{session_id}/approve`     | Approve — needs `device_mirror:approve`, which is **owner-only**                     |
| `POST`   | `/device-mirror/mirror-requests/{session_id}/deny`         | Deny                                                                                |
| `POST`   | `/device-mirror/mirror-requests/{session_id}/end`         | End a live session                                                                   |

Four gates stand between a request and a live view: the device is consented, the requester holds
`device_mirror:request`, the approver holds `device_mirror:approve` **and is a different person**,
and the session is bounded (30 minutes maximum, 10 by default). The self-approval rule is also a
database CHECK, so it holds even if every check in the service is bypassed. An administrator may
request but never approve. Withdrawing consent invalidates live sessions immediately.

### Realtime (`/stream/*`)

| Method | Path            | Description                                                                                        |
| ------ | --------------- | -------------------------------------------------------------------------------------------------- |
| `GET`  | `/stream/health` | Broker state — `redis` when Redis carries fan-out, else `degraded` (in-process hub)              |

Also `WS /api/v1/stream/ws` (absent from `openapi.json`, which does not model WebSockets). The
device authenticates the socket with its `X-Device-Key`; demand travels **both** ways and is
reconciled last-write-wins on `(location_id, date)`, matching the server's own rule. Forecasts are
server→device only — they carry model provenance (`as_of`, bundle) the device cannot assert.
Delivery is at-most-once and `device_events` is the durable record; a disconnected client catches
up by reading rather than by replay.

#### What a device may write

A device credential is a statement about one device in one tenant, so `POST /devices/{id}/demand`
takes every value that matters from the credential and nothing from the request body:

- **The device** comes from the credential. A path naming another device is 404.
- **The workspace** comes from the credential, so there is no field to aim at another tenant.
- **The location** comes from the device's own binding, never the body. A device enrolled against
  one location cannot be pointed at a sibling's forecast, and the request schema has no
  `location_id` to point it with.
- **A device with no location is refused (409)** rather than guessed at — there is no correct
  default, and picking one silently corrupts whichever was picked.
- The scope is the same `device:sync` that already authorises publishing demand over the
  WebSocket, so the socket and the REST write cannot drift into disagreeing about who may sync.

Rows go through the same `commit_demand` writer as every other adapter, which is what gives them
idempotency, last-write-wins reconciliation, the audit trail and the realtime push to connected
screens. A device with no second set of rules is a device that cannot be reasoned about.

## Health & Ops

| Method | Path             | Description                                                                                                                                               |
| ------ | ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET`  | `/health`        | **Public** Liveness probe — no DB check (`HealthResponse` `status: ok`, `version`)                                                                        |
| `GET`  | `/api/v1/health` | **Public** Detailed health — DB check (`database: ok\|unavailable`) via `SELECT 1`, plus `serving: {mode: trained\|stub\|error, …}`. A **set** but unloadable `MODEL_BUNDLE_PATH` returns **503** `status: degraded` (and the app refuses to boot); an unset path is the legitimate stub and stays 200 `status: ok`. |
| `GET`  | `/`              | **Public** Root — JSON for API clients (`{name, version, docs, health, console}`) or HTML console for browsers (`Accept: text/html` → `api-console.html`) |
| `GET`  | `/console`       | **Public** Gateway Console — serves `api-console.html` (live catalog 213 items)                                                                           |
| `GET`  | `/docs`          | **Public** Swagger UI (FastAPI auto-generated from 177 paths)                                                                                             |
| `GET`  | `/openapi.json`  | **Public** OpenAPI JSON — source for `backend/scripts/sync_console.py`                                                                                    |

Health is registered first at `backend/app/main.py:249` (`health.router`, no prefix) so it is never
shadowed by `/api/v1/locations/{id}`. Console/SPA mounting is last (`backend/app/main.py:333`).

> **Sync note:** After any router change, run `npm run sync:console`
> (`backend/scripts/sync_console.py`) to regenerate `api-console.html` from live `openapi.json` (155
> paths → 213 items). Verify with `npm run verify` (`scripts/verify.sh`).
