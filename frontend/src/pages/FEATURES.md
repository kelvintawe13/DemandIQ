# frontend/src/pages — Feature Pages

Documents the 10 major route pages (single-file views) that compose the DemandIQ web app. Each page maps to Ch 3.4-3.11 features and to one or more `backend/API_ENDPOINTS.md` resource groups. The directory itself is `Implemented prototype` (all `.tsx` files present); per-page detail below is the contract the backend must satisfy.

## Common Invariants (all pages)
- Auth: all pages except `Landing.tsx` require `Authorization: Bearer <jwt>`; admin pages require `Administrator` role.
- Reproducibility: any forecast shown carries `point, lower, upper, coverage_target, model_version_id, feature_set_version, as_of_timestamp` (Ch 3.26 leakage boundary; `database/schema.sql:227`).
- Evidence boundary (Ch 3.21, Fig 4): no page joins `demand_observations` with `waste_observations`; waste is shown only via `GET /waste/summary`.
- Failure surfaces use `components/ui/EmptyState.tsx` / `ErrorPage.tsx` per `backend/ARCHITECTURE.md` §8.
- **Keyboard:** `Modal` traps Tab inside the dialog, closes on Escape, and returns focus to the control that opened it; no interactive element is a bare `<div>`/`<span>` — the Webhooks row's delete was the last one. Icon-only buttons carry an `aria-label` and `tap-expand` for a 44px touch target. A `ResponsiveTable` row made selectable by `onRowClick` is also focusable and activates on Enter or Space (`rowAriaLabel` supplies what the user hears), so a keyboard user can pick a model in the registry without a pointer.
- **Contrast:** light-mode copy never uses `text-slate-400` (`#94a3b8`, ~2.6:1 on white, below WCAG AA); the passing grey is `text-slate-500` (`#64748b`, ~4.6:1). `dark:text-slate-400` is fine and stays. `__tests__/contrast.test.ts` scans the source and fails on a regression. **Dark mode is a single remap layer in `index.css`**, so a colour class only themes correctly if the layer names it — `__tests__/darkTheme.test.ts` scans the source and fails on an accent tint/ink the layer does not cover (with `SiteBanner`'s solid amber announcement bar as the one documented exception). Recharts paints its grid, axis ticks and tooltip with inline attributes/styles, so those four surfaces are pinned by name in the same test.
- **Accessibility smoke test:** `__tests__/axeAudit.test.tsx` renders all 27 screens (11 operator, 14 admin, landing and error) and runs axe-core over the result. It found what the behaviour suites could not: three icon-only buttons per location row, the Settings profile-photo button and the Policy Lab location `<select>` had no accessible name, and eight columns had an empty `<th>`. `region` and `color-contrast` are disabled with reasons in the file — the app supplies the landmark via `AppShell`, and jsdom cannot compute a rendered colour.
- **Motion:** `prefers-reduced-motion: reduce` neutralises the CSS animations and transitions (`animate-spin` spinners, `animate-pulse` skeletons, the modal `fadeIn`) in one place in `index.css`; the framer-motion surfaces already branched on `useReducedMotion`. Loaders stay legible — the skeleton keeps its shape and its `role="status"` name.
- **Loading:** a first load renders `components/ui/Skeleton.tsx` (`PageSkeleton`, matching the page's grid) rather than a lone spinner, with one `role="status"` carrying the same accessible name.
- **Mobile:** every data table uses `components/ui/Table.tsx` (`ResponsiveTable`), which restyles each row into a labelled card below `md` (`.responsive-cards` in `index.css`) rather than a table you drag sideways. The forecast table on `DemandForecast` and the policy comparison on `PolicyLab` were the last two raw `<table min-w-[720px]>` blocks and now use it too.
- **Empty states:** each `EmptyState` on an operator screen carries a primary action when the operator can act (Import upload tab, Sources' clear-search, connect-a-source) and turns into a hint naming who *can* act when the seat cannot (validation rules → owners/administrators).
- **Plain language:** every raw API token shown to a user goes through `lib/plainLanguage.ts` (`plainStatus`, `qualityDimensionLabel/Hint`, `modelLabel`). Statuses render as words (`committed` → `Imported`, `awaiting_mapping` → `Needs column mapping`), quality dimensions as the check they describe (Completeness / Freshness / Consistency / Accuracy), and a model UUID as `version abc12345`. Provenance footnotes read "Based on imported demand from X to Y" rather than "provenance: … model_version … demand_observations".

## Overview — `Overview.tsx` — `/overview`
**Status:** Implemented prototype  
**Proposal:** Landing surface for Fig 6 Use Case “View Dashboard”; surfaces Ch 3.8 EDA summaries and Ch 3.15 KPIs.  
**File map:** `Overview.tsx` + `components/overview/TodayPulseChart.tsx`  
**Contracts:** `GET /analytics/summary?from=&to=&location_id` (KPIs + previous-period delta), `GET /analytics/trend`, `GET /forecasts?location_id=&from=&to=` for today pulse, `GET /alerts?status=critical` badge.  
**Props/Data:** `TodayPulseChart { points: { hour, actual, forecast, lower, upper }[], coverageTarget }`; `StatusPill` for freshness.

## Analytics — `Analytics.tsx` — `/analytics`
**Status:** Implemented prototype  
**Proposal:** Ch 3.8 EDA, Ch 3.15 evaluation, Ch 3.20 policy trade visualisation.  
**File map:** `Analytics.tsx` + `components/analytics/DemandTrendChart.tsx`, `LocationDonut.tsx`, `Sparkline.tsx`  
**Contracts:** `GET /analytics/summary`, `/trend?granularity=daily|weekly`, `/by-location`, `/breakdown`, `/insights`; admin link to `GET /models/{id}/calibration` for coverage vs width.  
**Key props:** `DemandTrendChart { series: { date, value, previous }[], granularity }`; `LocationDonut { slices: { location, share }[] }`.

## DemandForecast — `DemandForecast.tsx` — `/forecast`
**Status:** Implemented prototype  
**Proposal:** Core Fig 5 flow: select restaurant → select forecast date → generate forecast → view interval → compare policies → inspect explanation (Ch 3.12-3.16, 3.24).  
**File map:** `DemandForecast.tsx` + `components/forecast/ForecastChart.tsx`, `PreparePanel.tsx`, `ForecastWhy.tsx` + `data/forecast.ts`  
**Guided flow (§3.22, in this order on one screen):** the control bar is numbered — *Step 1 · Which restaurant?*, *Step 2 · Which dates?*, *Step 3 · How to show it*, then *Generate Forecast* — and the decision layer sits directly beneath the KPIs: `PreparePanel` (three preparation rules in the operator's words, each with the quantity it implies and its surplus / unmet-demand exposure) beside `ForecastWhy` (per-forecast SHAP, with the predictive-not-causal framing). Both were previously reachable only from admin screens, so a planner never saw the forecast and the reason for it together.  
**Contracts:** `POST /forecasts { location_id, target_date, horizon_days, granularity }` → `{ point, lower, upper, coverage_target, model_version_id, as_of_timestamp }`; `GET /forecasts/{id}`, `GET /forecasts/{id}/explanation` (`{ feature, value, impact }` visitor units), `GET /forecasts/{id}/export?format=csv|xlsx`; `PreparePanel` → `POST /policies/simulate` (needs `policies:simulate`; the panel hides itself, rather than posting a decision the viewer may not make).  
**No invented numbers:** each KPI tile renders `—` and says what is missing when there is no forecast (the confidence tile used to show a fixed "87%", the range tile a hardcoded 80% width, and "Peak Day" the newest row while claiming to be the busiest — it is now computed from the loaded window). The interval's coverage comes from the forecast row, not a constant.  
**Invariants:** `as_of_timestamp` is server-set reproducibility boundary; interval is adaptive conformal (Gibbs & Candès 2021, Ch 3.16) nominal 90%; fallback `historical_avg` with fewer than 7 days of history, flagged `fallback:true` in the API and shown as a "Limited history" pill (not the raw flag).
**Plain language:** the forecast table's headers are `Day / Expected visitors / How sure / Likely range / Model / Method`; the footer reads "model version xxxxxxxx · this range holds about 9 days in 10" rather than `model: … coverage: 90% fallback=true`.

## ImportDemandData — `ImportDemandData.tsx` — `/import`
**Status:** Implemented  
**Proposal:** Ch 3.6 heterogeneous ingestion (MoMo, POS, CSV/XLSX, handwritten OCR, manual) → canonical `demand_observations(location_id,date,demand_qty)` (Fig 5 Data Ingestion & Normalisation).  
**Channels:** manual, spreadsheet (`ingest/file`) and photo/OCR are fully wired. POS / Mobile Money is wired to the Sources API; *creating* a channel still belongs to the Connectors page and needs `connectors:manage`.  
**File map:** `ImportDemandData.tsx` + `components/ingestion/ExcelCsvTab.tsx`, `ManualFormTab.tsx`, `PosMomoTab.tsx` + `SourceTabs.tsx`, `UploadImagePanel.tsx`  
**Contracts:** `POST /ingest/file` (multipart) → `{ upload_id, headers, preview[5] }`, `POST /ingest/file/{id}/mapping` (dry-run), `POST /ingest/file/{id}/commit` → `demand_observations`; `POST /ingest/manual`, `POST /ingest/image` → `{ job_id }` → `GET /ingest/image/{job_id}` → commit; `GET /ingest/uploads`, `DELETE /ingest/uploads/{id}`; `GET /ingest/demand`, `PATCH/DELETE /ingest/demand/{id}`; the POS/MoMo tab reads `GET /sources`, queues `POST /sources/{id}/sync` and reports `GET /sources/sync-history` (a sync is *queued*, not completed in-request; only an **active** channel is syncable — the API answers `409 not_active` otherwise, and the drained `connector_sync` job really pulls from the provider endpoint and commits the rows).  
**Commit rules:** `UNIQUE(location_id,date)` (`schema.sql:132`), `CHECK(demand_qty>=0)`, no future dates, `source_id`/`upload_id` retained, staged `ingest_uploads(status=parsing|awaiting_mapping|ready|committed)`.

## Sources — `Sources.tsx` — `/sources`
**Status:** Implemented prototype  
**Proposal:** Ch 3.6 connector registry + Ch 3.22 governance; surfaces `connectors` + `connector_mappings` + `api_keys`.  
**File map:** `Sources.tsx` + `components/illustrations/PosTerminal.tsx`  
**Contracts:** `GET /sources` (status/freshness/volume + each channel's own `last_pull` outcome), `POST /sources/{id}/sync` → `{ job_id }` (active channels only; `409 not_active` otherwise), `GET /sources/sync-history`; admin: `POST /connectors/{id}/pull` (`?dry_run=true` previews without writing; real pull needs an active, endpoint-configured channel), `GET /connectors`, `POST /connectors`, `PATCH/DELETE /connectors/{id}`, `POST /connectors/{id}/test`, `GET/PATCH /connectors/{id}/mapping`; `GET /api-keys`, `POST /api-keys` (secret once), `POST /api-keys/{id}/rotate`, `DELETE`.  
**Data:** `connectors(type in pos|momo|file|ocr|api|sms, status in active|paused|error|available)`, `connector_mappings(canonical_field in location_id|date|demand_qty|notes)`.

## QualityMonitor — `QualityMonitor.tsx` — `/quality`
**Status:** Implemented prototype  
**Proposal:** Ch 3.7 preprocessing checks + `ARCHITECTURE.md` §6 quality rules on every commit (gaps, spikes, cross-channel duplicates, stale connectors, schema drift).  
**File map:** `QualityMonitor.tsx` + `components/illustrations/DataSearch.tsx`  
**Contracts:** `GET /quality/score` (`{ overall, completeness, timeliness, consistency, accuracy }`), `GET /quality/issues` (severity critical|warning|info, `suggested_fix`), `POST /quality/issues/{id}/apply-fix`, `POST /quality/issues/{id}/dismiss` (reason), `GET/PATCH /quality/rules[/{id}]`.  
**Scoring:** issues link to `locations` and `pipeline_runs` for provenance.

## Alerts — `Alerts.tsx` — `/alerts`
**Status:** Implemented prototype  
**Proposal:** `ARCHITECTURE.md` §6 alerting on forecast error, coverage breach, drift, connector health; fans out in-app/email/webhook.  
**File map:** `Alerts.tsx` + `components/illustrations/StateArt.tsx`  
**Contracts:** `GET /alerts?status=unread|critical|resolved`, `POST /alerts/{id}/read` & `/read-all`, `POST /alerts/{id}/resolve` (note), `GET/PATCH /alerts/rules[/{id}]`. Index `alerts_open WHERE resolved_at IS NULL` (`schema.sql:328`).

## Reports — `Reports.tsx` — `/reports`
**Status:** Implemented prototype  
**Proposal:** `ARCHITECTURE.md` §6 async reporting to object store; `GET /analytics/*` aggregation for export.  
**File map:** `Reports.tsx` + `components/ui/DateRangeModal.tsx`, `LocationPickerModal.tsx`  
**Contracts:** `GET /reports/templates`, `POST /reports/generate { template_id, period, format, location_ids[] }` → `{ report_id, status:'queued' }`, `GET /reports/{id}` (adds a signed `download_url` once the worker has rendered the file, plus `size_bytes`, `content_type` and `error`), `GET /reports/{id}/download?expires&sig` (the bytes themselves), `GET /reports`, `GET /reports/schedules`, `POST/PATCH/DELETE /reports/schedules[/{id}]`.  
**Formats:** `format in (pdf,csv,xlsx)` (`reports.format CHECK`, `schema.sql:337`).
**Async:** generation only queues the row — the `report_generation` job renders it on its next tick and stores the file on the row. The page polls while a report is in flight, so a queued report turns into a Download on its own, and a failed one shows why instead of a bare status.

## Settings — `Settings.tsx` — `/settings`
**Status:** Implemented prototype  
**Proposal:** Account & workspace settings + Ch 3.22 governance (members/roles) + personal security (MFA, sessions, OAuth unlink).  
**File map:** `Settings.tsx` + `contexts/SessionContext.tsx`  
**Contracts:** `GET /auth/me`, `PATCH /me/profile`, `POST /me/avatar`, `POST /me/password`, `GET/PATCH /me/preferences` (theme, language, density), `GET/PATCH /me/notifications`, `POST /me/mfa/*`, `GET /me/sessions`, `DELETE /me/sessions/{id}`, `DELETE /me/identities/{provider}`; admin: `GET /members`, `POST /members/invite`, `PATCH /members/{id}`, `DELETE /members/{id}`, `GET /roles`, `GET /audit-log`.

## DeviceFleet — `DeviceFleet.tsx` — `/device-fleet`
**Status:** Implemented prototype  
**Proposal:** device fleet administration — the tills, kitchen screens and tablets a restaurant runs. Not `Devices.tsx`, which lists where *this person* is signed in.  
**File map:** `DeviceFleet.tsx` + `components/devices/*` (enrol, one-time secret, detail, permissions/grants, mirror consent) + `lib/deviceFleet.ts`.  
**Contracts:** `GET /devices?status=&location_id=` (the filters go to the server — a client-side copy would be a second answer to “which devices can this seat see”), `POST /devices` (the credential is returned once and never again), `PATCH /devices/{id}` (label + restaurant only), `POST /devices/{id}/rotate`, `POST /devices/{id}/quarantine|restore|revoke`, `GET /devices/{id}/effective-scopes`, `GET /devices/{id}/grants`, `GET /devices/{id}/events`.  
**Reading order:** KPI strip (devices · reporting · needs attention · never checked in) → the table with the attention list and the reference panel beside it → four charts (status mix, last-seen buckets, by restaurant, app versions). The three count charts draw horizontally (`layout="bars"`), because their labels — `1–24 hours`, `Not bound to a restaurant`, `Unknown version` — do not fit across a phone's upright axis. Every figure is derived from the same `rows` the table renders — the API-filtered list — so a tile or a chart cannot count a device the table does not show, and the panels' wording switches to “matching this filter” when one is applied.  
**`status` is not liveness.** A device is `active` from the moment it is enrolled, long before it sends anything, so “In service” beside “Never seen” were two answers that disagreed. `lib/deviceFleet.ts` keeps both the sentence (`lastSeen`) and the timestamp behind it (`lastSeenAt`, parsed once by `epochOf` so the label and the bucket cannot disagree), and `attentionFor` is the single place that decides what wants a person: never checked in, quiet for `STALE_AFTER_HOURS` (24), or quarantined. `revoked` is deliberately excluded — that decision has been taken, and the console says so on the page. `__tests__/deviceFleetOverview.test.tsx` pins the rules, their boundaries, and that the revoked and never-seen states are treated differently.

## Auth Pages (subfolder `auth/`)
See `auth/README.md` — flows `SignUp → VerifyEmail → AllSet`, `SignIn → SigningIn → TwoFactor`, `ResetPassword → CheckEmail` (CheckEmail is the password-reset "we sent a link" screen, not a sign-up step); contracts `POST /auth/register|verify-email|login|mfa/verify|oauth/google|password/forgot|reset`, `POST /auth/refresh|logout`.  
**Plain language:** sign-up copy speaks in operator terms ("Customers on a typical day", "Under 50 customers") and the offline demo hint on Verify Email appears only when the backend is actually unreachable.  
**`SigningIn` (OAuth callback):** the three steps are real reads — `GET /auth/me` (session), `GET /workspace` and `GET /alerts?status=unread` — each gated on the seat's capability and best-effort, so a failed read advances rather than stranding the user. `MIN_VISIBLE_MS` only keeps the screen legible when the backend answers instantly; it is not a per-step delay. Covered by `__tests__/signingIn.test.tsx`.

## Admin screens — plain language
`Connectors`, `Feature Store` and `Experiments` run the same `lib/plainLanguage` vocabulary as the operator pages: channel types and statuses are humanised, panel copy avoids "leakage checks" / "feature materialisation", and modelling acronyms (MAE, quantile) stay in `title` tooltips rather than on the face of the screen. Removing a connector now asks for confirmation. The gate is `__tests__/plainLanguage.test.ts` plus the "Operator screens speak plain language" block in `__tests__/dataClerkScreens.test.tsx`.

## Related Docs
- [API Contract](../../../backend/API_ENDPOINTS.md)
- [Architecture](../../../backend/ARCHITECTURE.md)
- [DB Schema](../../../database/schema.sql)
- [ML Contracts](../../../model/README.md)
- [Feature Spec](../../../model/FEATURE_SPEC.md)
- [Proposal](../../../project proposal.md)
