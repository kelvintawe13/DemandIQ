# DemandIQ — End-to-End Architecture

How a record travels from a vendor's till to a preparation decision on screen.
This document describes intended behaviour; the code in this folder is the
contract the implementation must satisfy.

## The shape of the system

```
 Operator surfaces          Edge              Services                 Stores
 ─────────────────   ───────────────   ─────────────────────   ─────────────────
 Web app (React)  ─┐                ┌─ Ingestion & Normalise ─┐
 Mobile / PWA     ─┼─► FastAPI ─────┼─ Forecasting            ├─► PostgreSQL
 Enrolled tills   ─┤   (REST +      ├─ Policy Simulation      ├─► Object store
 POS / MoMo hooks ─┤    auth +      ├─ Explainability         │   (uploads,
 Partner API      ─┘    rate limit) ├─ Quality & Alerts       │    artifacts)
                                    └─ Reporting / Webhooks   └─► Redis (queue,
                                                                   cache, rate)
```

Every service is reachable only through the API layer. Nothing in the UI talks
to a model or the database directly.

## 1. Ingestion — many shapes, one schema

The proposal's design envelope is six inbound channels; **five have an adapter in
this backend today**, and all five resolve to the same row:

| Channel | Transport | Normalisation work |
| --- | --- | --- |
| Manual entry | `POST /ingest/manual` | Validated row by row; a bad row is reported with its index and the good rows still commit |
| Spreadsheet | Upload (`POST /ingest/file`) | Sniff delimiter and encoding, find the header under title/date-range lines, skip `TOTAL`/blank/`n/a` furniture, apply the user's column mapping |
| Photo | Upload (`POST /ingest/image`) | Validate and decode, flatten the lighting, deskew, read candidate rows with the remote service or local Tesseract (re-grouping words into rows when a column-wise read split the dates from their counts), always human-confirmed before commit |
| Enrolled till | `POST /devices/{id}/demand` with `X-Device-Key` | Workspace and location come from the device credential, never from the request body |
| POS / MoMo connector | Polling pull (`POST /connectors/{id}/pull`) and the queued sync job | Map provider fields onto the canonical four through a stored `ConnectorMapping`, coercing types |

Mobile money is a connector type on that pull path, not a webhook: **no inbound
webhook writes demand**. `sms` is a reserved connector type with no parser, so
the proposal's SMS/USSD channel is not implemented.

The canonical row is `(workspace_id, location_id, date, demand_qty, notes,
recorded_at)`. There is no `source` column: the channel is written as a
`[source:<tag>]` note, beside `upload_id` or `source_id` where one applies, and
`GET /ingest/demand` reads the tag back as `source`.

Every one of the five channels also owns a **row in `connectors`**, because
`GET /sources` is a view over that table: without one, a workspace that imported
everything by spreadsheet or by hand had a full demand series and "No sources
connected" on the screen that is meant to answer "where did this come from?".
`services/ingestion/channels.py` creates the channel on first use —
`Spreadsheet uploads` (`file`), `Photo uploads` (`ocr`), `Manual entry`
(`manual`), `Till devices` (`pos`) — stamps `last_sync_at` and the
`last_pull_*` counts from the import, and tags each row with its `source_id`.
Those channels are `available`, never `active`: `_job_connector_sync` pulls every
active connector from the endpoint in its credentials, and an import has no
endpoint to pull.

Commit rules, enforced in one place
(`services/ingestion/normaliser.py::commit_demand`) so no channel can bypass them:

1. **Idempotent by `(location_id, date)`.** A second submission for a day that
   already exists is a *reconciliation*, not an insert: the newer value wins, and
   the previous quantity is returned as `previous_qty`, logged, and fanned out to
   devices so they apply the same last-write-wins rule.
2. **No future dates.** A row dated after the workspace's local service day is
   rejected, because "has tomorrow started here?" is a local question, not a UTC one.
3. **The quantity is bounded** to `0 … 10,000,000` — a typo guard, not a storage limit.
4. **Every row keeps its channel**, as the note tag above, so the quality monitor
   can attribute it without a JOIN.
5. **Uploads are staged, not committed.** Parsed rows sit in `ingest_uploads`
   with a preview until the operator confirms in the UI; deleting the upload
   removes the rows it wrote.

A committed row is published on the demand topic so connected tills update
immediately. That publish is best-effort: a realtime failure never fails an
ingestion that has already reached the database.

## 2. Feature build — the leakage boundary

A nightly job (and an on-demand path for a single forecast) materialises
features for each `(location_id, date)`. Every feature is computed with an
explicit `as_of` timestamp equal to the moment the forecast would be made.

- **Historical:** lag-1, lag-7, rolling 7/28-day mean, same-weekday mean,
  28-day volatility.
- **Calendar:** day of week, weekend flag, month, public holiday, day before /
  after a holiday.
- **Location:** service type, district, seating capacity.
- **Reservation (supplementary):** counts and lead times, only where a
  connector supplies them, and only as known before `as_of`.

The rule the whole system rests on: **a feature may only read rows with
`date < target_date`.** Rolling windows are computed on a shifted series, and
the job refuses to run if any feature's source window overlaps the target day.

## 3. Forecasting

For a requested `(location, date)`:

1. Load the production model version from the registry.
2. Assemble features; if the location has under 30 days of history, short-circuit
   to a historical-average response flagged `fallback: true`.
3. Produce the point forecast.
4. Produce the interval with adaptive conformal inference: the running
   miscoverage error updates a single step-size parameter, so intervals widen
   after a run of surprises and tighten when the series settles.
5. Persist to `forecasts` with the model version, feature-set version and
   `as_of_timestamp`, so any number on screen can be reproduced exactly.

Responses always carry `point`, `lower`, `upper`, `coverage_target`,
`model_version` and `fallback`. The UI never receives a bare number.

## 4. Policy simulation — forecast to decision

The forecast alone does not tell an operator how much to prepare. The policy
engine converts it under three transparent rules:

| Policy | Preparation quantity |
| --- | --- |
| Historical average | mean of the last *n* comparable days |
| Point forecast | `P = D̂` |
| Uncertainty-aware | `P = Q(q)`, the upper `q` quantile of the interval |

Both exposures are reported, because reporting only one produces a trivial
result:

- `SR = max(0, P − D̂)` — exposure relative to the forecast that set the policy.
- `PS = max(0, P − D)` — exposure against what actually happened.
- `UD = max(0, D − P)` — unmet demand.

Raising `q` reduces `UD` and increases `PS`. The API returns both sides of that
trade so the interface can show it rather than assert a winner. These are
visitor-equivalent planning quantities, never kilograms of measured waste.

## 5. Explainability

SHAP values are computed per forecast and stored alongside it, so the
explanation shown months later is the explanation that was actually generated —
not a re-derivation against a newer model. Responses are labelled as predictive
attribution, not causal effect.

## 6. Quality, alerts and reporting

**Quality rules are declared, not evaluated.** `GET /quality/rules` seeds the
five rule records the UI shows — gaps in the series, implausible spikes,
duplicate-day conflicts across channels, stale connectors and schema drift — and
an admin can enable or disable them, but nothing reads that flag: no check runs
on commit or in a job, and no code path inserts a `QualityIssue`. `GET
/quality/issues` is therefore limited to rows inserted by hand, and `GET
/quality/score` starts at 100, subtracts per open issue, and reports the
remaining dimensions as fixed offsets of that same number.

**Alerts** come from two paths only: the hourly `drift_check` job, which raises
one alert per incident when drift crosses its threshold, and a failed pipeline
job, which is resolved by that job's next success. Alert *rules* are CRUD —
create, edit, delete — with no evaluator behind them. A raised alert appears
in-app. Its webhook fan-out writes one delivery row per subscribed endpoint
without transmitting anything yet, and no alert is emailed: the `smtp_*`
settings are read only to decide whether email verification is required.

**Reports are rendered by a worker.** `POST /reports/generate` inserts the row as
`queued` and returns; the `report_generation` job (every minute, five rows per
tick) reads the workspace's own tables, renders the document in the requested
format and stores the bytes on the row (`reports.content`, migration 022),
then marks it `completed`. A render that throws marks the row `failed` and keeps
the reason in `reports.error`, so a bad template is visible on `GET /reports/{id}`
rather than only in a job log.

Each template reports data this system actually holds: demand rows, forecasts
scored against realised demand, persisted policy simulations, and uploads beside
demand coverage. A template with nothing to report says so — the waste report
prints "no policy simulation covers this period" instead of zeros that read as
results.

`GET /reports/{id}/download` serves the stored bytes, authorised by an expiring
HMAC link (`expires` + `sig`) because the browser opens it directly; the link is
`null` until the file exists.

## 7. Governance

Anything that changes what ships — promoting a model, changing the preparation
quantile, granting admin access — becomes a change request. The requester
cannot approve their own request; the constraint is enforced in the database,
not only in application code. Every privileged action is written to the audit
log with the actor, the client (web, mobile, API) and the before/after value.

## 8. Failure behaviour

| Failure | Behaviour |
| --- | --- |
| Model service unavailable | Serve historical-average with `fallback: true`; never a blank screen |
| Connector down | Last good data stays; freshness badge degrades; alert raised |
| Client offline | Mobile queues entries locally and replays them; idempotency makes the replay safe |
| Upload malformed | Rejected at the staging step with the offending row and column named |
| Model output implausible | Guardrail clamps to a sane range and flags the forecast for review |

Every one of these surfaces in the UI through the shared `EmptyState` or
`ErrorPage` component rather than a silent empty panel.

## Non-functional targets

- Forecast read: p95 under 300 ms (served from `forecasts`, not recomputed).
- On-demand forecast: p95 under 2 s.
- Nightly batch for 10k locations: under 20 minutes.
- Ingestion write acknowledgement: p95 under 500 ms; pulls and OCR runs are
  queued as background jobs.

## Measuring OCR accuracy

Recognition accuracy on handwriting is measured, not assumed.
`scripts/ocr_accuracy.py` runs the real extraction path over a directory of
photos and a verified reference sheet (`image,date,demand_qty[,box]`) and reports,
per page and in total: dates recalled, counts matched, complete date-count pairs,
missing / spurious / duplicated / misattributed rows, highlight coverage and IoU
against any marked box, and seconds per page. `--self-check` renders printed
pages and scores them, which proves the harness is wired correctly — it is not a
claim about handwriting, and the only meaningful number comes from verified
photos of real sheets.
