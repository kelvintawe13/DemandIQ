# DemandIQ Database

PostgreSQL schema backing the API in `../backend`. Reference DDL for ORM models
(`backend/app/models/__init__.py`) and Alembic migrations
(`backend/migrations/versions/014_devices.py`). A migrated database holds **54 tables**
= 41 data + 8 agent + 4 billing + `platform_settings`.

## Tech Stack

- **Database**: PostgreSQL 15+
- **Extensions**: `pgcrypto` + `citext` (`database/schema.sql:4`) — UUID + case-insensitive email
- **ORM**: SQLAlchemy 2.0 (backend, 41 mapped classes over the 41 `schema.sql` tables)
- **Migrations**: Alembic, single head — `001_initial` → `002_user_phone_prefs` → `003_billing` →
  {`004_locations_extra_fields`, `004_serving_state`} → `005_agent_governance` →
  `006_user_profile_completion` → `007_schema_drift_backfill` → `008_site_admin` →
  `009_site_admin_flag` → `010_merge_heads` → `011_risk_aware_policy` →
  `012_forecast_fallback` → `014_devices`

## Schema Files

- `schema.sql` — 530 lines — 37 data tables + 4 billing (`database/schema.sql`). It matches
  `001_initial` and is **not** updated by later migrations (that is the strategy below), so it
  documents 53 of the 54 tables a migrated database holds.
- `AGENT_SCHEMA.sql` — 169 lines — 8 `agent_*` tables (quota, sessions, turns, pins, guardrails)
- `backend/migrations/versions/014_devices.py` — latest migration (`sessions.ip_country` → `sessions.origin`)

## Design Rules

1. **Demand and waste never join** — `demand_observations` and `waste_observations` share no FK (`schema.sql:134`, `schema.sql:286`)
2. **Forecasts are immutable** — Past predictions never updated, only compared (`forecasts.as_of_timestamp` NOT NULL, `schema.sql:239`)
3. **Leakage safety is recorded** — Every forecast stores `feature_set_version` and `as_of_timestamp`
4. **Everything privileged is audited** — All changes write to `audit_log` (`backend/app/middleware/audit.py`)
5. **Soft delete only** — Locations and members soft-deleted to preserve history

## Key Tables

- `workspaces` — Tenant boundary
- `users` — Accounts, roles, MFA state (`CITEXT` email, `schema.sql:24`)
- `locations` — Restaurants, canteens, vendor sites
- `connectors` — POS, MoMo, file, OCR and API channels
- `demand_observations` — Canonical daily demand fact (`UNIQUE(location_id,date)`, `schema.sql:144`)
- `model_versions` — Trained artefacts with metrics
- `forecasts` — Point forecast plus interval (`as_of_timestamp`, `schema.sql:228`)
- `preparation_policies` — Historical-average, point-forecast, uncertainty-aware, risk-aware
  (`policy_kind` enum at `schema.sql:253`; `quantile` / `lookback_days` / `risk_weight` are the
  per-kind parameters)
- `change_requests` — Two-person approval queue (`CHECK(decided_by<>requested_by)`, `schema.sql:418`)
- `audit_log` — Immutable record of privileged actions (`middleware/audit.py`)
- `billing_subscriptions`, `billing_invoices`, `billing_payment_methods`, `billing_contacts` — Billing (`003_billing`, `schema.sql:446`)
- `agent_quota_tiers`, `agent_seat_quotas`, `agent_quota_grants`, `agent_usage_daily`, `agent_sessions`, `agent_turns`, `agent_pins`, `agent_guardrails` — Assistant governance (8 tables, `AGENT_SCHEMA.sql:9`)
- `location_access_logs`, `user_preferences`, `user_notification_settings` — Access + prefs
- `platform_settings` — Platform-administrator flags (`008_site_admin`). Migration-only: absent
  from `schema.sql` and not ORM-mapped, so it is the one table a fresh `schema.sql` load will not
  create.

Count: **54 in a migrated database** = 41 data + 8 agent (`AGENT_SCHEMA.sql`) + 4 billing
(`schema.sql:446`) + `platform_settings`. `schema.sql` alone gives 45, plus the 8 agent tables = 53.

## Setup

```bash
# Start postgres + redis (DATABASE_URL overridden in docker-compose.yml:47)
docker compose up -d postgres redis

# Apply migrations — THE schema, creates all 51 tables
cd backend && python3.11 -m alembic upgrade head
# head is 014_devices

# Verify
psql -U postgres -d demandiq -c "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename!='alembic_version';"
# expect 50 after `alembic upgrade head` (alembic_version excluded)
# expect 49 if you loaded schema.sql + AGENT_SCHEMA.sql by hand (no platform_settings)
```

> **Alembic owns the schema.** `schema.sql` and `AGENT_SCHEMA.sql` are the readable
> reference for what those migrations produce, not a parallel way to build a
> database. They used to be mounted into postgres's `/docker-entrypoint-initdb.d`,
> which created every table at boot and then made the `alembic upgrade head` that
> `scripts/dev.sh` runs next fail with `relation "workspaces" already exists` —
> so `npm run dev` did not work on a fresh clone. Applying them by hand to an
> *empty* database still works and is useful for reading, but do not load them
> into a database you also intend to migrate.

Frontend client is `frontend/src/api/client.ts:1` (`VITE_API_URL` at `docker-compose.yml:73`); API
is `backend/app/main.py:135` (177 paths → 213 items, `backend/scripts/sync_console.py`).

## Migration Strategy

When implementing the backend, the first Alembic migration matched `schema.sql` exactly.
Subsequent changes go through Alembic migrations, not direct DDL changes. The chain ends at
`014_devices` (`backend/migrations/versions/014_devices.py`), which renames
`sessions.ip_country` to `sessions.origin` — the column was named for a country code but has only
ever held `request.client.host`, and `/me/sessions` hands that value straight back to the client.
The rename is the whole fix: the value is unchanged, it is now named what it is. Before it,
`012_forecast_fallback` records
whether a forecast's point came from the model or is a historical average, so every read path
reports the same provenance. Before that, `011_risk_aware_policy` adds the
`risk_aware` member of the `policy_kind` enum and the `risk_weight` cost-ratio column on
`preparation_policies` (§3.17). The chain is not linear: `004_locations_extra_fields` and
`004_serving_state` both descend from `003_billing` and are rejoined by `010_merge_heads`, so
`alembic heads` must be checked against a single head rather than assumed. Separately,
`003_billing` added the 4 billing tables (`schema.sql:446`).

## Important Constraints

- **Two-person rule**: `change_requests` has `CHECK(decided_by<>requested_by)` (`schema.sql:418`)
- **Leakage boundary**: Features never read target day data (enforced in ML `feature_build.py`, not DB)
- **Evidence boundary**: Waste data never joined to demand (`schema.sql:134`, `schema.sql:286`, `GET /waste/summary` only)
- **Extensions**: `pgcrypto` + `citext` (`schema.sql:4`) — UUID generation + case-insensitive email
- **Counts**: a migrated database has 54 tables (41 data + 8 agent + 4 billing + `platform_settings`); `database/schema.sql` + `AGENT_SCHEMA.sql` document 53 of them. Verify with `npm run verify`
- **Alembic is the only way to create a database you intend to migrate** — see the note above
