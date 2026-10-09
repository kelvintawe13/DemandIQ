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
 SMS / USSD       ─┤   (REST +      ├─ Policy Simulation      ├─► Object store
 POS / MoMo hooks ─┤    auth +      ├─ Explainability         │   (uploads,
 Partner API      ─┘    rate limit) ├─ Quality & Alerts       │    artifacts)
                                    └─ Reporting / Webhooks   └─► Redis (queue,
                                                                   cache, rate)
```

Every service is reachable only through the API layer. Nothing in the UI talks
to a model or the database directly.

## 1. Ingestion — many shapes, one schema

Six inbound channels exist, and they all resolve to the same row:

| Channel | Transport | Normalisation work |
| --- | --- | --- |
| POS connector | OAuth + polling | Group transactions by service day, drop refunds and voids, count covers |
| Mobile money | Webhook | Same grouping; payer identity is discarded at the edge |
| Spreadsheet | Upload | Sniff delimiter and encoding, parse dates, apply the user's column mapping |
| Handwritten photo | Upload | OCR to candidate rows, always human-confirmed before commit |
| Manual entry | REST | Direct, still validated |
| SMS / USSD | Gateway | Parse `DATE COVERS` shorthand, reply with a confirmation |

The canonical row is `(location_id, date, demand_qty, source, recorded_at)`.

Commit rules, enforced in one place so no channel can bypass them:

1. **Idempotent by `(location_id, date)`.** A second submission for a day that
   already exists is a *reconciliation*, not an insert. The newer value wins and
   the prior value is kept in the audit trail.
2. **No future dates.** A row dated after the service day is rejected.
3. **Every row keeps its source.** Traceability is required for the quality
   monitor and for any later dispute.
4. **Uploads are staged, not committed.** Parsed rows sit in `ingest_uploads`
   with a preview until the operator confirms in the UI.

Ingestion emits `ingestion.completed` or `ingestion.failed`, which drives the
quality monitor, alerts and outbound webhooks.

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

Quality rules run on every commit: gaps in the series, implausible spikes,
duplicate-day conflicts across channels, stale connectors, and schema drift.
Failures raise scored issues with a suggested fix. Alert rules watch forecast
error, coverage breaches, drift against the training distribution, and
connector health, and fan out in-app, by email, and to webhooks.

Reports are generated asynchronously into the object store; the API returns a
job id, and the UI polls or waits for the completion event.

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
- Ingestion webhook acknowledgement: under 500 ms, processing asynchronous.
