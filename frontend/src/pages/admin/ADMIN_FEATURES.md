# frontend/src/pages/admin — Admin Console Features

Administrator-only console (`Administrator` role per `backend/API_ENDPOINTS.md` § Models/Governance). All routes are `Implemented prototype` in React; backend is `Specification only` (see `backend/README.md`). Implements Ch 3.12-3.16 models & uncertainty, Ch 3.17-3.20 policy simulation, Ch 3.21 waste boundary, and Ch 3.22 governance (change_requests two-person rule, `database/schema.sql:405`).

## Common Admin Invariants
- **Auth:** every request carries `Authorization: Bearer <jwt>` + `role=administrator`; otherwise 403.
- **Two-person rule:** `change_requests.decided_by <> requested_by` (`schema.sql:405`) — Approve disabled on own request; enforced in DB, not just UI.
- **Reproducibility:** every forecast/model row shows `model_version_id`, `feature_set_version`, `as_of_timestamp` (`schema.sql:227`).
- **Evidence boundary:** `GET /waste/summary` never joins to `demand_observations` (Fig 4, `waste_observations` has no FK, `schema.sql:268-271`).

## ModelRegistry — `ModelRegistry.tsx` — `/admin/models`
**Purpose:** Ch 3.12-3.13 model registry (LightGBM/CatBoost primary, XGBoost/RF/linear/historical_avg baselines).  
**Contracts:** `GET /models` (rows `algorithm, MAE, RMSE, RMSLE, status in training|candidate|production|archived|failed`), `GET /models/{id}` (hyperparams, train/test windows, `artifact_uri`), `POST /models/train { algorithm, feature_set, params }`, `POST /models/{id}/promote` → change_request, `POST /models/{id}/rollback`, `GET /models/{id}/calibration` (target vs empirical coverage, mean width), `GET /models/{id}/importance`.  
**Data:** `model_versions` (`schema.sql:159`), `calibrations` (`schema.sql:180`), `feature_set_version` pinned (e.g. `fs-2026.09.3`).

## Experiments — `Experiments.tsx` — `/admin/experiments`
**Purpose:** Ch 3.11 chronological validation + Ch 3.15 metrics + Ch 3.25 sensitivity (backtests).  
**Contracts:** `GET /experiments`, `POST /experiments { name, hypothesis, algorithm, feature_set, folds }`, `GET /experiments/{id}` (fold MAE/coverage, baseline delta), `POST /experiments/{id}/submit` → approvals queue.  
**Data:** `experiments` + `experiment_folds` (`schema.sql:190-212`); no random splits, expanding/rolling-origin only on train; test never used for tuning.

## FeatureStore — `FeatureStore.tsx` — `/admin/features`
**Purpose:** Ch 3.10 feature engineering + `model/FEATURE_SPEC.md` (information boundary `as_of = t-1@23:59`, no feature reads `date>=t`).  
**Contracts:** `GET /features` (`name, feature_group, dtype, coverage_pct, leakage_safe, in_production`), `GET /features/{name}/coverage` per-location.  
**Data:** `feature_definitions` (`schema.sql:146`); groups: historical (`lag_1/7/14`, `roll_mean_7/28`, `roll_std_28`, `dow_mean_8w`), calendar (`dow`, `is_weekend`, `month`, `is_holiday`, `day_before/after_holiday`), location (`service_type`, `district`), reservation supplementary (38.2% coverage). Mismatch `feature_set_version` is hard error.

## ForecastExplainer — `ForecastExplainer.tsx` — `/admin/explainer`
**Purpose:** Ch 3.24 explainability (SHAP, predictive not causal).  
**Contracts:** `GET /forecasts/{id}/explanation` → `{ feature, value, impact }` signed visitor units, `GET /models/{id}/importance` aggregates.  
**Data:** `forecast_explanations(feature_name, feature_value, shap_impact)` (`schema.sql:233`); `GET /forecasts/{id}` carries `model_version_id` for correct explainer version.  
**Live:** SHAP contributions come from `GET /forecasts/{id}/explanation` (stored per-forecast by `model/explain/shap_values.py`); no local fixture.

## PolicyLab — `PolicyLab.tsx` — `/admin/policies`
**Purpose:** Ch 3.17-3.20 preparation policies + `ARCHITECTURE.md` §4 (Historical avg / Point / Uncertainty-aware `P=Q(q)`).  
**Contracts:** `GET /policies` (three configured policies), `POST /policies/simulate { location_id, period, policies[], quantile }` → `{ surplus_sr, surplus_ps, unmet_ud, surplus_days, unmet_days }` (visitor-equivalents, not kg), `PATCH /policies/{id}` (admin → change_request).  
**Formulas:** `SR=max(0,P-D̂)` forecast-relative, `PS=max(0,P-D)` realised, `UD=max(0,D-P)`; raising `q` reduces `UD`, increases `PS` (Ch 3.17 trade). Persisted to `policy_simulations` (`schema.sql:252`).

## Connectors — `Connectors.tsx` — `/admin/connectors`
**Purpose:** Ch 3.6 ingestion channels → canonical `demand_observations`; `ARCHITECTURE.md` §1.  
**Contracts:** `GET /connectors` (type `pos|momo|file|ocr|api|sms`, status), `POST /connectors`, `PATCH/DELETE /connectors/{id}`, `POST /connectors/{id}/test`, `POST /connectors/{id}/pull` (`?dry_run=true`), `GET/PATCH /connectors/{id}/mapping` (`source_field → canonical_field in location_id|date|demand_qty|notes`).  
**Pull preview:** the pull endpoint answers `{ pulled, committed, skipped, rows, errors, detail, run_id }`. A row's *Preview pull* action calls it with `dry_run=true`, which fetches and maps the provider's rows but writes none of them; the panel shows the rows a real pull would commit, the count ready vs skipped, and each skip reason. A refusal (no endpoint, `409 not_configured`) is a toast, not an empty panel — the panel is for a pull that ran.  
**Test run:** the test endpoint answers `{ latency_ms, status, checks:[{name, ok, detail}], sample_payload, checked_at }`. All of it stays on screen in a "Test run" panel — every precondition with its pass/fail and detail, when it ran, and the canonical *schema* the connector must produce (`sample_payload`, whose placeholder names the mapped source field and its type, e.g. `date → <YYYY-MM-DD>`).  
**Data:** `connectors` + `connector_mappings` (`schema.sql:87-104`), `ingest_uploads` staging.

## LocationRegistry — `LocationRegistry.tsx` — `/admin/locations`
**Purpose:** Location master + history depth/forecastability (Ch 3.7 gaps: 85% avg coverage, 210 stores <80%).  
**Contracts:** `GET /locations` (history depth, avg covers, connector, forecastability), `POST /locations` (admin), `GET /locations/{id}` (ingestion history + gaps), `PATCH /locations/{id}`, `DELETE /locations/{id}` (archive, never hard delete, `archived_at`).  
**Data:** `locations` (`schema.sql:64`), `reservations UNIQUE(location_id,date)` (`schema.sql:135`), `demand_observations UNIQUE(location_id,date)`.
**UI:** `StatCard` strip (sites, forecasting, seats, menu groups) → table on `RecordList` → menu groups → onboarding rule. History is drawn against the 7-day bar `lag_7`/`roll_mean_7` need. **Adding is a route, editing is a dialog:** "Add location" navigates to `AddLocation` (`/admin-locations-new`, a sub-route of this one — see below), while a row's pencil opens the edit `FormModal`. Both render `components/locations/LocationFields.tsx`, so the four sections (Identity / Trading / Contact / Notes) cannot drift between them. The detail dialog leads with forecast readiness, then the reference fields.  
**Manager and phone are editable in the row** (`InlineEdit`, `components/ui/InlineEdit.tsx`): a correction unrelated to the rest of the record should not cost an eleven-field dialog. One click, one `PATCH { <field> }`, and the row is rewritten from the read model the API returns rather than from what was typed. Clearing sends `''`, never `null` — `PATCH /locations` applies a field only when it is not `None`, so a `null` would leave the old number stored while the list showed it gone. `__tests__/adminDataFields.test.tsx` covers both, plus the refused write that puts the old value back.  
**Three states, not two.** `GET /locations` answers `forecastable: null` and `history_days: null` for a site the pipeline has not scored. Reading that as `true` made every such row say "forecasting" and the panel say "All set." — neither of which the API said, and the same class of defect `__tests__/adminDataFields.test.tsx` exists to catch. Unscored sites now read `not scored`, carry no history bar (an empty bar means zero days, not unknown), and the panel says `N not scored yet.` The same rule covers `open_days_mask: null` ("Open days not recorded", never "Open every day").

## AddLocation — `AddLocation.tsx` — `/admin-locations-new`
**Purpose:** Onboard one site (Ch 3.2 register + Ch 3.7 history depth).  
**Contracts:** `POST /locations` (admin). Same payload as the edit dialog (`lib/locations.ts: locationPayload`).  
**Route:** Declared as a sub-route of `admin-locations` in `data/navItems.ts` (`SUB_ROUTE_PARENT`, which also inherits the parent's permissions for the access check) and in `data/pages.ts`. Sub-routes are addressable but never listed in the sidebar, and the parent stays highlighted (`aria-current`) while the child renders. The URL is the point: the first thing a new workspace does should survive a reload, and Back should leave the form.  
**UI:** the grouped fields, an action bar that lives inside the same `<form>` (so Enter submits from any field), and two side panels — “The site you are adding” (a live summary of the draft) and “What happens on save” (Day 0 / Day 7 / any time, and which weekdays the pattern excludes). Nothing is written until submit; there is no partial save.  
**Tests:** `__tests__/addLocationRoute.test.tsx` — the four groups render, submit writes once and returns to the registry, an unnamed site writes nothing, and Back leaves without writing.

## DatasetsAdmin — `DatasetsAdmin.tsx` — `/admin/datasets`
**Purpose:** Ch 3.4 dataset auditing + Ch 3.21 URBAN-WASTE independent characterisation + Ch 3.28 provenance (licences).  
**Contracts:** `GET /datasets` (Recruit Kaggle competition conditions vs URBAN-WASTE CC BY 4.0), `GET /datasets/{id}/manifest` (checksums, date range 2016-01-01→2017-04-22 vs 2018 pilots, preprocessing version), `GET /waste/summary` descriptive stats (never joined; 3,321 records, 3,160 waste values, 34 establishments, 9 with ordered_portions), including `establishment_variation` — the per-kitchen spread behind the mean, bounded by the API to the 10 busiest, with `establishments` giving the total so the panel can say how much of the dataset it covers.  
**Data:** `waste_observations` (`schema.sql:273`) deliberate no FK — boundary Fig 4.

## AiGovernance — `AiGovernance.tsx` / `Approvals.tsx` — `/admin/governance` + `/admin/approvals`
**Purpose:** Ch 3.22 governance: promotions, policy changes, access grants as `change_requests` + `audit_log`.  
**Contracts:** `GET /change-requests` (pending with before/after diff), `POST /change-requests/{id}/approve` | `/reject` (requester may not approve own), `GET /audit-log?actor=&action=&client=&date` (client `web|mobile|api|system`), `GET /members`, `POST /members/invite`, `PATCH /members/{id}`, `DELETE /members/{id}`, `GET /roles` (permission matrix).  
**Data:** `change_requests(kind in model_promotion|policy_change|access_grant, two_person_rule)` + `audit_log` (`schema.sql:390-430`).

## PipelineHealth — `PipelineHealth.tsx` — `/admin/pipeline`
**Purpose:** Job observability + drift + schedule (`ARCHITECTURE.md` §6-9, Ch 3.31 Docker reproducibility).  
**Contracts:** `GET /pipeline/runs` (`job_type ingestion|training|calibration|forecasting`, `status success|queued|running|failed`, `rows_processed`, `duration_ms`), `POST /pipeline/runs/{id}/rerun`, `GET /pipeline/drift` (live vs training), `GET /pipeline/schedule` (per job: `cadence`, `cron`, `last_run`, `next_run`), `POST /pipeline/jobs/{job_type}/run` (run one now), `POST /pipeline/queue/drain` (complete queued runs without waiting for a worker tick). The Schedule panel renders one row per job — cadence, last-run status, next tick — each with a Run now button; a queued run makes a Drain button appear on the runs panel. `lib/schedule.ts` holds the shared `ScheduleRow` type and `nextRunLabel`/`jobLabel` helpers, also used by the Sources page's Ingestion cadence panel.  
**Data:** `pipeline_runs` (`schema.sql:408`) — one row per execution, written `running` then `success`/`failed`; a `running` row with no completion past `STALE_RUN_MINUTES` (15) is reaped as abandoned. A failed run raises one open `Pipeline job failed: <job_type>` alert and fans out `alert.raised`; the next success resolves it with `alert.resolved`. Nightly batch 10k locations <20 min, forecast read p95 <300 ms, on-demand <2 s.  
**Empty state:** no cadence is invented — if `GET /pipeline/schedule` fails, the panel shows "Schedule unavailable" with Retry (a data clerk without `PIPELINE_READ` sees no panel on Sources at all).

## AccessControl — `AccessControl.tsx` — `/admin-access`
**Purpose:** Workspace members + roles/permissions (`workspaces`, `users`, `user_role` enum owner|administrator|analyst|operator|viewer, `schema.sql:17`; `operator` is displayed as **Data Clerk**). The capability matrix is rendered from `GET /roles`, which is generated by `backend/app/security/permissions.py` — the single source of truth the API also enforces. Scope per role: the owner runs the business (members, approvals, billing, oversight) but not the modelling console; the administrator runs the models and the platform; the data clerk enters and cleans data; the analyst adds analysis, simulation and reports; the viewer only reads.  
**Contracts:** `GET /members`, `POST /members/invite` (`email`, `role`, `name`), `PATCH /members/{id}` (`role` and/or `name` — the route's own docstring is "change role or name"), `DELETE /members/{id}` (revoke, soft `deactivated_at`), `GET /roles` (+ `groups`), `GET /audit-log` (`actor_id`, `client`, `limit`, `cursor`).  
**Three API facts the page is built around:** `GET /members` takes **no cursor** (the whole roster in one `data` array), so the tab deliberately has **no pager** — the only paged read is the trail. `POST /members/invite` stores **no location scope**, so the dialog asks for none and says so; a scope select that was silently discarded used to sit in it. And the trail is **cursor-paginated**, so its counts describe the loaded window and say which: `across all 51 recorded actions`, or `the last 100 privileged actions` when the read was capped by `AUDIT_WINDOW`.  
**Layout:** a `StatCard` strip (members · awaiting invite · privileged actions · refused — the last being what an owner opens the page for), then one `TabBar` of three tabs. *Members*: `CountChart` of the roster by role, a `MixDonut` of accepted vs outstanding invitations, and the roster on the shared `FilterBar`/`RecordList` (role and invitation filters, quick role picker per row, edit dialog, revoke confirmed in `ConfirmDialog` rather than `window.confirm`). *Roles*: capabilities per role and per area, then the full matrix with a sticky header and card mode on a phone. *Activity*: the trail with **server-side** `actor_id`/`client` filters that each re-read rather than narrowing the page in hand, real cursor paging (Newer/Older), then the surface donut and most-frequent-action bars.  
**Two lists, two totals:** the per-role counts come from each role's own `permissions`; the area bars come from the API's published `groups`. They do not cover the same set — 42 held against 40 listed, three held capabilities under no area and one listed capability (`device:sync`) held by no role — so `lib/accessControl.capabilityGaps` finds the difference and the panel states it instead of leaving two panels to disagree.  
**Invariant:** `DELETE /me/mfa` rejected for `Administrator`; unlink `DELETE /me/identities/{provider}` rejected if only credential.

## Webhooks — `Webhooks.tsx` — `/admin/webhooks`
**Purpose:** Outbound events (`forecast.generated`, `policy.changed`, `alert.raised`, etc.) signed `X-DemandIQ-Signature` HMAC-SHA256, 5 retries exp backoff.  
**Contracts:** `GET/POST /webhooks` (`url`, `events[]`), `PATCH/DELETE /webhooks/{id}`, `POST /webhooks/{id}/test` (signed sample), `GET /webhooks/{id}/deliveries` (response codes), `GET /webhooks/events` catalog.  
**Test delivery:** the test endpoint answers `{ event, attempt, response_code, delivered_at, payload, signature_header }`. All of it stays on screen in a "Test delivery" panel: the code the endpoint returned, the attempt, the sample payload, and the `X-DemandIQ-Signature` header an integrator must recompute the HMAC against.  
**Data:** `webhook_endpoints` + `webhook_deliveries` (`schema.sql:358-376`).

## Related Docs
- [API Contract](../../../../backend/API_ENDPOINTS.md)
- [Architecture](../../../../backend/ARCHITECTURE.md)
- [DB Schema](../../../../database/schema.sql)
- [ML Contracts](../../../../model/README.md)
- [Feature Spec](../../../../model/FEATURE_SPEC.md)
- [Proposal](../../../../project proposal.md)
