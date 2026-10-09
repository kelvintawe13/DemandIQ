# DemandIQ — Implementation Roadmap

Which documents to read, in which order, and what to build in which sequence.
The prototype in this repository is the specification for the interface; these
documents are the specification for everything behind it.

## Document map

| Document | Answers |
| --- | --- |
| `backend/ARCHITECTURE.md` | How a record travels from a kitchen to a forecast |
| `backend/IMPLEMENTATION_GUIDE.md` | Stack, layout, conventions, build phases |
| `backend/API_ENDPOINTS.md` | Every route and its contract |
| `backend/DATA_CONTRACTS.md` | Payload shapes, errors, idempotency, pagination |
| `backend/AGENT_SERVICE.md` | The assistant: quotas, sessions, guardrails |
| `database/README.md` | Design rules and the entity map |
| `database/schema.sql` | Core DDL |
| `database/AGENT_SCHEMA.sql` | Assistant governance DDL |
| `ml/README.md` | Pipeline layout and function contracts |
| `ml/MODEL_LIFECYCLE.md` | Training through promotion and monitoring |
| `ml/FEATURE_SPEC.md` | Every predictor, its definition and its leakage rule |
| `ml/EVALUATION.md` | How a model is judged and what gates promotion |
| `ml/AGENT_DESIGN.md` | What the assistant may say and how it is grounded |

## Sequence

Each milestone leaves something demonstrable. Do not run them in parallel
until milestone 3 is done, because everything after it depends on a committed
demand row existing.

**1 — Foundations (week 1–2).** Settings, database, migrations, container,
health endpoint, CI with the Postgres integration harness.

**2 — Identity (week 2–3).** Register, sign in, refresh, TOTP, Google OAuth,
Turnstile verification, password reset, sessions, API keys with scopes. Rate
limit the auth routes before anything else is exposed publicly.

**3 — Workspace and ingestion (week 3–6).** Workspaces, locations, members,
roles. Then the ingestion adapters in this order: manual entry, spreadsheet
upload with staging and column mapping, POS/MoMo connectors, OCR, SMS. Every
adapter must terminate at the same `commit_demand()` function.

**4 — Features and forecasting (week 6–9).** The `as_of` boundary and the
leakage test first — before any model exists, so it cannot be retrofitted.
Then materialisation, baselines, the primary model, conformal intervals, and
the historical-average fallback.

**5 — Decisions (week 9–10).** The three preparation policies, both exposure
metrics, and the policy frontier. This is the milestone where the product
stops being a chart and starts being a decision.

**6 — Trust surfaces (week 10–12).** SHAP per forecast stored alongside it,
the quality rule engine, alerts, and reporting.

**7 — Governance (week 12–13).** Change requests, the two-person rule enforced
in the database, and audit middleware on every privileged route.

**8 — Assistant (week 13–15).** Read-only tool surface, planner and composer,
both guardrail passes, quota and session enforcement, the admin console.
Build the guardrails before the planner; a model wired to tools with no
postflight check is the one thing in this system that can lie convincingly.

**9 — Distribution (week 15–16).** Webhooks with HMAC signing and retry, the
partner API, and scheduled report delivery.

## Non-negotiables

These are not phase-specific; violating any of them invalidates the product's
central claim.

1. **No feature may read the target day.** Enforced by a test that fails the
   build, not by review.
2. **Every forecast carries an interval and a model version.** A bare number
   never leaves the API.
3. **Surplus is simulated exposure in visitor-equivalents.** The system never
   claims measured food waste.
4. **URBAN-WASTE data is never joined row-level to demand data.** It
   characterises the problem; it does not label the training set.
5. **A stored forecast must be reproducible** from its recorded model version
   and feature-set version.
6. **Nobody approves their own change.** Enforced by a database constraint.

## Definition of done, per surface

A screen in the prototype is considered implemented when:

- every state it can reach is served by a real endpoint, including empty,
  loading, error and offline;
- the numbers on it carry their provenance (model version, window, source);
- its privileged actions are audited;
- its failure mode degrades rather than blanks — stale data with an honest
  freshness badge beats an empty panel.
