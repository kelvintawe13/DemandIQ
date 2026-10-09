# backend/app/models

## Status

**Fully implemented** — 118 py, 41 model classes across 20 modules, 49 tables (530-line
`database/schema.sql` + `AGENT_SCHEMA.sql`),
`014_devices` (`backend/app/models/__init__.py`, `backend/migrations/versions/014_devices.py`).

## Purpose & Proposal Alignment

SQLAlchemy ORM models mapping 1:1 to `database/schema.sql` (530 lines) + `database/AGENT_SCHEMA.sql`
(8 tables). No business logic; only column definitions, relationships, and DB-level constraints
(UNIQUE, CHECK, two-person rule). Mirrors ERD Fig 10 (Ch 3.23). Registered in
`backend/app/models/__init__.py`.

Chapters: **3.4-3.11** features & ingestion, **3.12-3.15** models & metrics, **3.16** uncertainty,
**3.17-3.20** policy SR/PS/UD, **3.21** evidence boundary, **3.22** architecture
(`backend/app/main.py:135`).

## File Map

| Model                                                                                                                               | Table                                                                                      | Notable Constraint                                                                              |
| ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| `Workspace`, `User`, `Session`, `UserIdentity`, `MfaRecoveryCode`                                                                   | `workspaces`, `users`, `sessions`, `user_identities`, `mfa_recovery_codes`                 | `users.email CITEXT UNIQUE`, `user_role` enum (`schema.sql:2` `citext`/`pgcrypto`)              |
| `Location`, `MenuGroup`, `Connector`, `ConnectorMapping`                                                                            | `locations`, `menu_groups`, `connectors`, `connector_mappings`                             | `connector_type/status` enums                                                                   |
| `IngestUpload`, `DemandObservation`, `Reservation`                                                                                  | `ingest_uploads`, `demand_observations`, `reservations`                                    | `demand_observations UNIQUE(location_id,date)` (`schema.sql:132`), `CHECK(demand_qty>=0)`       |
| `FeatureDefinition`, `ModelVersion`, `Calibration`, `Experiment`, `ExperimentFold`                                                  | `feature_definitions`, `model_versions`, `calibrations`, `experiments`, `experiment_folds` | `model_status` enum, `feature_set_version`                                                      |
| `Forecast`, `ForecastExplanation`                                                                                                   | `forecasts`, `forecast_explanations`                                                       | `forecasts.as_of_timestamp` NOT NULL (`schema.sql:227`), index `(location_id,target_date DESC)` |
| `PreparationPolicy`, `PolicySimulation`                                                                                             | `preparation_policies`, `policy_simulations`                                               | `policy_kind` enum, `SR/PS/UD` numerics                                                         |
| `WasteObservation`                                                                                                                  | `waste_observations`                                                                       | **No FK to `locations`/`demand_observations`** — evidence boundary Fig 4 (`schema.sql:277`)     |
| `QualityRule`, `QualityIssue`, `AlertRule`, `Alert`                                                                                 | `quality_*`, `alerts`                                                                      | `alerts_open WHERE resolved_at IS NULL`                                                         |
| `Report`, `ReportSchedule`, `WebhookEndpoint`, `ApiKey`, `ChangeRequest`, `PipelineRun`, `AuditLog`                                 | `reports*`, `webhook*`, `api_keys`, `change_requests`, `pipeline_runs`, `audit_log`        | `two_person_rule CHECK(decided_by<>requested_by)` (`schema.sql:405`)                            |
| `BillingSubscription`, `BillingInvoice`, `BillingPaymentMethod`, `BillingContact`                                                   | `billing_subscriptions`, `billing_invoices`, `billing_payment_methods`, `billing_contacts` | `003_billing` — 4 tables (`schema.sql:437`)                                                     |
| `LocationAccessLog`, `UserPreferences`, `UserNotificationSettings`                                                                  | `location_access_logs`, `user_preferences`, `user_notification_settings`                   | Access + prefs                                                                                  |
| `AgentQuotaTier`, `AgentSeatQuota`, `AgentQuotaGrant`, `AgentUsageDaily`, `AgentSession`, `AgentTurn`, `AgentPin`, `AgentGuardrail` | `agent_*` (8 tables)                                                                       | `AGENT_SCHEMA.sql` — quota + sessions + guardrails                                              |

All models use `gen_random_uuid()` PKs except `demand_observations`/`forecast_explanations`
(BIGSERIAL). Workspace-scoped FKs cascade on delete. Total **49 tables** = 37 data + 8 agent + 4
billing (`schema.sql`).

## Key Props / Contracts / Data Sources

DB invariants: `demand_observations UNIQUE(location_id,date)` (`database/schema.sql:132`),
`forecasts.as_of_timestamp` NOT NULL (`schema.sql:227`), evidence boundary `waste_observations`
with no FK (`schema.sql:277`), two-person rule `CHECK(decided_by<>requested_by)` (`schema.sql:405`).

ML contracts: `predict(location_id, target_date, as_of) -> Forecast { point, lower, upper,
coverage_target, model_version_id }` and `simulate(...) -> PolicyResult { surplus_sr, surplus_ps,
unmet_ud }` per `model/README.md`. Client is `frontend/src/api/client.ts:1`.

## Related Docs

- [API Contract](../../API_ENDPOINTS.md) — 177 paths → 213 items
- [Architecture](../../ARCHITECTURE.md)
- [DB Schema](../../../database/schema.sql) — 530 lines
- [Agent Schema](../../../database/AGENT_SCHEMA.sql) — 8 tables
- [ML Contracts](../../../model/README.md)
- [Feature Spec](../../../model/FEATURE_SPEC.md)
- [Proposal](../../../project\ proposal.md)
- [API Console](../../api-console.html) — live 213 items
