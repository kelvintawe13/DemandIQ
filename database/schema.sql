-- DemandIQ reference schema (PostgreSQL 15+)
-- Specification only: implement as the first Alembic migration.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "citext";

-- ---------------------------------------------------------------- tenancy

CREATE TABLE workspaces (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            TEXT NOT NULL,
  business_type   TEXT,
  country         TEXT NOT NULL DEFAULT 'RW',
  timezone        TEXT NOT NULL DEFAULT 'Africa/Kigali',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TYPE user_role AS ENUM ('owner', 'administrator', 'analyst', 'operator', 'viewer');

CREATE TABLE users (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name                  TEXT NOT NULL,
  email                 CITEXT NOT NULL UNIQUE,
  password_hash         TEXT,
  role                  user_role NOT NULL DEFAULT 'operator',
  job_title             TEXT,
  phone                 TEXT,
  avatar_url            TEXT,
  mfa_enabled           BOOLEAN NOT NULL DEFAULT false,
  mfa_secret            TEXT,
  preferences           JSONB,
  notification_settings JSONB,
  email_verified        BOOLEAN NOT NULL DEFAULT false,
  deactivated_at        TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_identities (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider        TEXT NOT NULL,              -- 'google'
  provider_uid    TEXT NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (provider, provider_uid)
);

CREATE TABLE mfa_recovery_codes (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  code_hash       TEXT NOT NULL,
  used_at         TIMESTAMPTZ
);

CREATE TABLE sessions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  refresh_hash    TEXT NOT NULL,
  device          TEXT,
  -- Where the sign-in came from: the client address as recorded by the auth
  -- router (``origin``; named ``ip_country`` before migration 013, which never
  -- held a country code).
  origin          TEXT,
  trusted_device  BOOLEAN NOT NULL DEFAULT false,
  last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at      TIMESTAMPTZ
);

-- ------------------------------------------------------------- operations

CREATE TABLE locations (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id      UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name              TEXT NOT NULL,
  area              TEXT,
  cuisine           TEXT,
  opened_on         DATE,
  seating_capacity  INT CHECK (seating_capacity > 0),
  service_type      TEXT,
  district          TEXT,
  open_days_mask    TEXT,
  address           TEXT,
  phone             TEXT,
  manager_name      TEXT,
  notes             TEXT,
  archived_at       TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE menu_groups (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  item_count      INT NOT NULL DEFAULT 0,
  cover_share     NUMERIC(5,2),
  perishability   TEXT NOT NULL CHECK (perishability IN ('high','medium','low'))
);

CREATE TYPE connector_type AS ENUM ('pos','momo','file','ocr','api','sms');
CREATE TYPE connector_status AS ENUM ('active','paused','error','available');

CREATE TABLE connectors (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  type            connector_type NOT NULL,
  name            TEXT NOT NULL,
  status          connector_status NOT NULL DEFAULT 'available',
  credentials     JSONB,                      -- encrypted at rest
  last_sync_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE connector_mappings (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  connector_id    UUID NOT NULL REFERENCES connectors(id) ON DELETE CASCADE,
  source_field    TEXT NOT NULL,
  canonical_field TEXT NOT NULL CHECK (canonical_field IN ('location_id','date','demand_qty','notes')),
  transform       TEXT
);

CREATE TYPE upload_status AS ENUM ('parsing','awaiting_mapping','ready','committed','failed');

CREATE TABLE ingest_uploads (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  uploaded_by     UUID REFERENCES users(id),
  kind            TEXT NOT NULL CHECK (kind IN ('csv','xlsx','image')),
  filename        TEXT NOT NULL,
  status          upload_status NOT NULL DEFAULT 'parsing',
  row_count       INT,
  error_count     INT NOT NULL DEFAULT 0,
  preview         JSONB,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The canonical demand fact table. Every source normalises into this shape.
CREATE TABLE demand_observations (
  id              BIGSERIAL PRIMARY KEY,
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  location_id     UUID NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  date            DATE NOT NULL,
  demand_qty      INT NOT NULL CHECK (demand_qty >= 0),
  source_id       UUID REFERENCES connectors(id),
  upload_id       UUID REFERENCES ingest_uploads(id),
  notes           TEXT,
  recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (location_id, date)
);

CREATE TABLE reservations (
  id              BIGSERIAL PRIMARY KEY,
  location_id     UUID NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  date            DATE NOT NULL,
  reserved_count  INT NOT NULL DEFAULT 0,
  lead_time_days  NUMERIC(5,2),
  UNIQUE (location_id, date)
);

-- ------------------------------------------------------------- modelling

CREATE TABLE feature_definitions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            TEXT NOT NULL UNIQUE,
  feature_group   TEXT NOT NULL,
  dtype           TEXT NOT NULL,
  description     TEXT NOT NULL,
  coverage_pct    NUMERIC(5,2),
  leakage_safe    BOOLEAN NOT NULL DEFAULT true,
  in_production   BOOLEAN NOT NULL DEFAULT false
);

CREATE TYPE model_status AS ENUM ('training','candidate','production','archived','failed');

CREATE TABLE model_versions (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  algorithm           TEXT NOT NULL,          -- lightgbm | catboost | xgboost | rf | linear | historical_avg
  version             TEXT NOT NULL,
  status              model_status NOT NULL DEFAULT 'candidate',
  mae                 NUMERIC(8,3),
  rmse                NUMERIC(8,3),
  rmsle               NUMERIC(8,4),
  hyperparameters     JSONB,
  feature_set_version TEXT NOT NULL,
  train_start         DATE,
  train_end           DATE,
  test_start          DATE,
  test_end            DATE,
  artifact_uri        TEXT,
  trained_at          TIMESTAMPTZ,
  promoted_at         TIMESTAMPTZ,
  UNIQUE (workspace_id, algorithm, version)
);

CREATE TABLE calibrations (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  model_version_id    UUID NOT NULL REFERENCES model_versions(id) ON DELETE CASCADE,
  method              TEXT NOT NULL DEFAULT 'adaptive_conformal',
  target_coverage     NUMERIC(5,2) NOT NULL DEFAULT 90.00,
  empirical_coverage  NUMERIC(5,2),
  mean_interval_width NUMERIC(8,3),
  evaluated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE experiments (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name                TEXT NOT NULL,
  hypothesis          TEXT,
  config              JSONB NOT NULL,
  status              TEXT NOT NULL DEFAULT 'draft',
  mae                 NUMERIC(8,3),
  baseline_mae        NUMERIC(8,3),
  coverage            NUMERIC(5,2),
  created_by          UUID REFERENCES users(id),
  finished_at         TIMESTAMPTZ
);

CREATE TABLE experiment_folds (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_id       UUID NOT NULL REFERENCES experiments(id) ON DELETE CASCADE,
  fold_index          INT NOT NULL,
  train_start         DATE, train_end DATE,
  test_start          DATE, test_end DATE,
  mae                 NUMERIC(8,3),
  coverage            NUMERIC(5,2)
);

-- ------------------------------------------------------------- forecasting

CREATE TABLE forecasts (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  location_id         UUID NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
  model_version_id    UUID NOT NULL REFERENCES model_versions(id),
  target_date         DATE NOT NULL,
  point_forecast      NUMERIC(10,2) NOT NULL,
  lower_bound         NUMERIC(10,2) NOT NULL,
  upper_bound         NUMERIC(10,2) NOT NULL,
  coverage_target     NUMERIC(5,2) NOT NULL DEFAULT 90.00,
  feature_set_version TEXT NOT NULL,
  as_of_timestamp     TIMESTAMPTZ NOT NULL,   -- information boundary, for reproducibility
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX forecasts_lookup ON forecasts (location_id, target_date DESC);

CREATE TABLE forecast_explanations (
  id                  BIGSERIAL PRIMARY KEY,
  forecast_id         UUID NOT NULL REFERENCES forecasts(id) ON DELETE CASCADE,
  feature_name        TEXT NOT NULL,
  feature_value       TEXT,
  shap_impact         NUMERIC(10,3) NOT NULL,  -- visitor units, signed
  -- Which path wrote the row: the served booster's TreeSHAP, or the labelled
  -- placeholder used when no model is loaded (migration 020). The two cannot be
  -- told apart from the numbers, and only one of them is model evidence.
  attribution_source  TEXT NOT NULL DEFAULT 'stub'
    CONSTRAINT ck_forecast_explanations_attribution_source
    CHECK (attribution_source IN ('booster', 'stub'))
);

CREATE TYPE policy_kind AS ENUM ('historical_average','point_forecast','uncertainty_aware','risk_aware');

CREATE TABLE preparation_policies (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  kind            policy_kind NOT NULL,
  quantile        NUMERIC(4,3),               -- only for uncertainty_aware
  lookback_days   INT,                        -- only for historical_average
  risk_weight     NUMERIC(4,2),               -- only for risk_aware (stock-out / leftover cost)
  is_active       BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE policy_simulations (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  policy_id       UUID NOT NULL REFERENCES preparation_policies(id),
  location_id     UUID REFERENCES locations(id),
  period_start    DATE NOT NULL,
  period_end      DATE NOT NULL,
  prepared_total  NUMERIC(12,2) NOT NULL,
  surplus_sr      NUMERIC(12,2) NOT NULL,     -- max(0, P - forecast)
  surplus_ps      NUMERIC(12,2) NOT NULL,     -- max(0, P - realized)
  unmet_ud        NUMERIC(12,2) NOT NULL,     -- max(0, realized - P)
  surplus_days    INT NOT NULL,
  unmet_days      INT NOT NULL,
  simulated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --------------------------------------------- independent waste evidence
-- Deliberately has NO foreign key to locations or demand_observations.
-- URBAN-WASTE is characterised on its own terms and must never be joined
-- row-level to demand data (Figure 4, evidence boundary).

CREATE TABLE waste_observations (
  id                  BIGSERIAL PRIMARY KEY,
  establishment_code  TEXT NOT NULL,
  country             TEXT,
  observed_on         DATE,
  waste_kg            NUMERIC(10,3),
  ordered_portions    INT,
  source_dataset      TEXT NOT NULL DEFAULT 'urban-waste-ftd',
  licence             TEXT NOT NULL DEFAULT 'CC BY 4.0'
);

-- ------------------------------------------------------- quality & alerts

CREATE TABLE quality_rules (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  description     TEXT,
  enabled         BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE quality_issues (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  rule_id         UUID REFERENCES quality_rules(id),
  location_id     UUID REFERENCES locations(id),
  severity        TEXT NOT NULL CHECK (severity IN ('critical','warning','info')),
  summary         TEXT NOT NULL,
  suggested_fix   TEXT,
  resolved_at     TIMESTAMPTZ,
  dismissed_at    TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE alert_rules (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  threshold       JSONB,
  enabled         BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE alerts (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  rule_id         UUID REFERENCES alert_rules(id),
  location_id     UUID REFERENCES locations(id),
  severity        TEXT NOT NULL CHECK (severity IN ('critical','warning','info')),
  title           TEXT NOT NULL,
  body            TEXT,
  read_at         TIMESTAMPTZ,
  resolved_at     TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX alerts_open ON alerts (workspace_id) WHERE resolved_at IS NULL;

-- ------------------------------------------------------------- reporting

CREATE TABLE reports (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  template        TEXT NOT NULL,
  period_start    DATE, period_end DATE,
  format          TEXT NOT NULL CHECK (format IN ('pdf','csv','xlsx')),
  status          TEXT NOT NULL DEFAULT 'queued',
  file_uri        TEXT,
  size_bytes      BIGINT,
  generated_by    UUID REFERENCES users(id),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE report_schedules (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  template        TEXT NOT NULL,
  cadence         TEXT NOT NULL,              -- daily | weekly | monthly
  recipients      TEXT[] NOT NULL,
  format          TEXT NOT NULL,
  enabled         BOOLEAN NOT NULL DEFAULT true,
  next_run_at     TIMESTAMPTZ
);

-- ------------------------------------------------------------- delivery

CREATE TABLE webhook_endpoints (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  url             TEXT NOT NULL,
  events          TEXT[] NOT NULL,
  secret          TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'active',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE webhook_deliveries (
  id              BIGSERIAL PRIMARY KEY,
  endpoint_id     UUID NOT NULL REFERENCES webhook_endpoints(id) ON DELETE CASCADE,
  event           TEXT NOT NULL,
  payload         JSONB NOT NULL,
  response_code   INT,
  attempt         INT NOT NULL DEFAULT 1,
  delivered_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE api_keys (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  label           TEXT NOT NULL,
  prefix          TEXT NOT NULL,
  key_hash        TEXT NOT NULL,
  scopes          TEXT[] NOT NULL,
  last_used_at    TIMESTAMPTZ,
  revoked_at      TIMESTAMPTZ
);

-- ------------------------------------------------------------ governance

CREATE TABLE change_requests (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL,              -- model_promotion | policy_change | access_grant
  title           TEXT NOT NULL,
  before_value    TEXT,
  after_value     TEXT,
  impact          TEXT,
  status          TEXT NOT NULL DEFAULT 'pending',
  requested_by    UUID NOT NULL REFERENCES users(id),
  decided_by      UUID REFERENCES users(id),
  decided_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- two-person rule: a requester may not decide their own request
  CONSTRAINT two_person_rule CHECK (decided_by IS NULL OR decided_by <> requested_by)
);

CREATE TABLE pipeline_runs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  job_type        TEXT NOT NULL,              -- ingestion | training | calibration | forecasting
  status          TEXT NOT NULL,              -- success | running | failed
  rows_processed  BIGINT,
  duration_ms     INT,
  detail          TEXT,
  started_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
  id              BIGSERIAL PRIMARY KEY,
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  actor_id        UUID REFERENCES users(id),
  action          TEXT NOT NULL,
  target          TEXT,
  client          TEXT NOT NULL CHECK (client IN ('web','mobile','api','system')),
  metadata        JSONB,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX audit_log_recent ON audit_log (workspace_id, created_at DESC);

-- ------------------------------------------------------------ billing (workspace subscription)
CREATE TABLE billing_subscriptions (
  id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id          UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE UNIQUE,
  plan_id               TEXT NOT NULL,
  status                TEXT NOT NULL DEFAULT 'active',
  current_period_start  TIMESTAMPTZ NOT NULL DEFAULT now(),
  current_period_end    TIMESTAMPTZ NOT NULL,
  cancel_at_period_end  BOOLEAN NOT NULL DEFAULT false,
  trial_end             TIMESTAMPTZ,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE billing_invoices (
  id                TEXT PRIMARY KEY,
  workspace_id      UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  subscription_id   UUID NOT NULL REFERENCES billing_subscriptions(id) ON DELETE CASCADE,
  period            TEXT NOT NULL,
  period_start      TIMESTAMPTZ,
  period_end        TIMESTAMPTZ,
  amount_cents      INT NOT NULL DEFAULT 0,
  amount_display    TEXT NOT NULL DEFAULT 'RWF 0',
  currency          TEXT NOT NULL DEFAULT 'RWF',
  status            TEXT NOT NULL DEFAULT 'paid',
  pdf_url           TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE billing_payment_methods (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE UNIQUE,
  brand       TEXT NOT NULL DEFAULT 'visa',
  last4       TEXT NOT NULL DEFAULT '4242',
  exp_month   INT NOT NULL DEFAULT 9,
  exp_year    INT NOT NULL DEFAULT 2028,
  is_default  BOOLEAN NOT NULL DEFAULT true,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE billing_contacts (
  workspace_id UUID PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
  email       TEXT NOT NULL DEFAULT 'finance@demandiq.app',
  tin         TEXT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------ location access (phase 1)
-- Workspace-scoped view tracking for locations — every list/detail/forecast/policy view writes here.
CREATE TABLE location_access_logs (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id    UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  location_id     UUID REFERENCES locations(id) ON DELETE CASCADE,
  user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
  action          TEXT NOT NULL CHECK (action IN ('view','list','detail','forecast','policy')),
  ip_address      TEXT,
  user_agent      TEXT,
  client          TEXT NOT NULL CHECK (client IN ('web','mobile','api','system')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_location_access_logs_workspace_created ON location_access_logs (workspace_id, created_at DESC);
CREATE INDEX ix_location_access_logs_location_created ON location_access_logs (location_id, created_at DESC);
CREATE INDEX ix_location_access_logs_user_created ON location_access_logs (user_id, created_at DESC);

-- ------------------------------------------------------------ per-user preferences (normalized mirror of users.preferences)
CREATE TABLE user_preferences (
  user_id             UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  theme               TEXT NOT NULL DEFAULT 'light',
  language            TEXT NOT NULL DEFAULT 'en',
  density             TEXT NOT NULL DEFAULT 'comfortable',
  default_location_id UUID REFERENCES locations(id) ON DELETE SET NULL,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_notification_settings (
  user_id       UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  email_digest  BOOLEAN NOT NULL DEFAULT true,
  push_alerts   BOOLEAN NOT NULL DEFAULT true,
  in_app_alerts BOOLEAN NOT NULL DEFAULT true,
  weekly_report BOOLEAN NOT NULL DEFAULT false,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- device fleet
-- Enrolled customer hardware (tills, KDS screens, sensors). `credential_hash`
-- is a bcrypt hash of the secret half of an `X-Device-Key`; the secret itself is
-- shown once at enrolment and never stored, and `credential_prefix` is the
-- non-secret half used to find the row before the hash is checked. The UNIQUE
-- pair is per-workspace, so a prefix is unique inside a tenant and may repeat
-- across tenants.
CREATE TABLE devices (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id        UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  location_id         UUID REFERENCES locations(id) ON DELETE SET NULL,
  label               TEXT NOT NULL,
  device_type         TEXT NOT NULL DEFAULT 'pos',
  platform            TEXT NOT NULL DEFAULT 'unknown',
  app_version         TEXT,
  credential_prefix   TEXT NOT NULL,
  credential_hash     TEXT NOT NULL,
  scopes              TEXT[] NOT NULL DEFAULT '{}',
  public_key          TEXT,
  status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'revoked', 'quarantined')),
  last_seen_at        TIMESTAMPTZ,
  enrolled_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  enrolled_by         UUID REFERENCES users(id) ON DELETE SET NULL,
  revoked_at          TIMESTAMPTZ,
  -- Stamped when the customer agrees to consent-gated remote viewing. A device
  -- without it can never be mirrored, whatever the session state says.
  mirroring_consent_at TIMESTAMPTZ,
  CONSTRAINT uq_devices_workspace_prefix UNIQUE (workspace_id, credential_prefix)
);

CREATE INDEX ix_devices_workspace_id ON devices (workspace_id);
CREATE INDEX ix_devices_location_id ON devices (location_id);

-- A capability granted on top of the device's own `scopes`. A revoked row is
-- kept, not deleted: it is the record of the withdrawal, so `revoked_at`,
-- `revoked_by` and `reason` stay populated.
CREATE TABLE device_grants (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  device_id    UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  capability   TEXT NOT NULL,
  granted_by   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  granted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at   TIMESTAMPTZ,
  revoked_at   TIMESTAMPTZ,
  revoked_by   UUID REFERENCES users(id) ON DELETE SET NULL,
  reason       TEXT
);

CREATE INDEX ix_device_grants_workspace_id ON device_grants (workspace_id);
CREATE INDEX ix_device_grants_device_id ON device_grants (device_id);
CREATE INDEX ix_device_grants_device_capability ON device_grants (device_id, capability);

-- Append-only device telemetry. Highest-volume table in the system, and the one
-- a remote device can write to, so `payload` is bounded by an allowlist in the
-- router rather than trusted.
CREATE TABLE device_events (
  id           BIGSERIAL PRIMARY KEY,
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  device_id    UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  event_type   TEXT NOT NULL,
  payload      JSONB NOT NULL DEFAULT '{}',
  recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_device_events_workspace_id ON device_events (workspace_id);
CREATE INDEX ix_device_events_device_id ON device_events (device_id);
CREATE INDEX ix_device_events_device_recorded ON device_events (device_id, recorded_at);

-- A requested, consented, time-boxed remote viewing session. The two-person
-- rule is a table constraint rather than application logic so that a request
-- and its approval cannot be signed by the same person even if every check in
-- the service is bypassed. `ends_at` is the hard ceiling, not a duration the
-- holder may extend.
CREATE TABLE mirror_sessions (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  device_id    UUID NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
  requested_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  decided_by   UUID REFERENCES users(id) ON DELETE CASCADE,
  status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'active', 'ended', 'denied')),
  requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at   TIMESTAMPTZ,
  ends_at      TIMESTAMPTZ,
  ended_at     TIMESTAMPTZ,
  consent_at   TIMESTAMPTZ,
  reason       TEXT,
  CONSTRAINT ck_mirror_sessions_two_person_rule
    CHECK (decided_by IS NULL OR decided_by <> requested_by)
);

CREATE INDEX ix_mirror_sessions_workspace_id ON mirror_sessions (workspace_id);
CREATE INDEX ix_mirror_sessions_device_id ON mirror_sessions (device_id);
CREATE INDEX ix_mirror_sessions_device_status ON mirror_sessions (device_id, status);
