-- DemandIQ — assistant governance schema
-- Extends database/schema.sql. Everything here is scoped to a workspace and
-- deletes with it. The assistant never writes to domain tables; these tables
-- exist purely to meter, limit and audit it.

-- ---------------------------------------------------------------------------
-- Quota tiers
-- ---------------------------------------------------------------------------
CREATE TABLE agent_quota_tiers (
    id                  TEXT PRIMARY KEY,
    name                TEXT        NOT NULL,
    daily_questions     INTEGER     NOT NULL CHECK (daily_questions   > 0),
    monthly_questions   INTEGER     NOT NULL CHECK (monthly_questions > 0),
    concurrent_sessions SMALLINT    NOT NULL CHECK (concurrent_sessions BETWEEN 1 AND 10),
    -- Tools this tier may invoke. Always a subset of what the role permits.
    allowed_tools       TEXT[]      NOT NULL,
    description         TEXT        NOT NULL,
    CONSTRAINT monthly_at_least_daily CHECK (monthly_questions >= daily_questions)
);

-- ---------------------------------------------------------------------------
-- Seat assignment
-- ---------------------------------------------------------------------------
CREATE TABLE agent_seat_quotas (
    workspace_id    UUID    NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id         UUID    NOT NULL REFERENCES users(id)      ON DELETE CASCADE,
    tier_id         TEXT    NOT NULL REFERENCES agent_quota_tiers(id),
    -- NULL means "use the tier default". Set only for deliberate exceptions.
    daily_override  INTEGER CHECK (daily_override > 0),
    status          TEXT    NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'throttled', 'suspended')),
    updated_by      UUID    REFERENCES users(id),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);

-- ---------------------------------------------------------------------------
-- Temporary grants
-- Additive top-ups. Stored as rows rather than mutations of the tier, so the
-- original allowance is always recoverable and the grant is auditable.
-- ---------------------------------------------------------------------------
CREATE TABLE agent_quota_grants (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    -- NULL user_id means the grant applies to every seat matching scope_role.
    user_id      UUID REFERENCES users(id) ON DELETE CASCADE,
    scope_role   TEXT,
    extra_daily  INTEGER     NOT NULL CHECK (extra_daily > 0),
    issued_by    UUID        NOT NULL REFERENCES users(id),
    reason       TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    CONSTRAINT grant_targets_someone CHECK (user_id IS NOT NULL OR scope_role IS NOT NULL),
    CONSTRAINT grant_expires_later   CHECK (expires_at > created_at)
);

CREATE INDEX agent_grants_active_idx
    ON agent_quota_grants (workspace_id, expires_at DESC);

-- ---------------------------------------------------------------------------
-- Consumption
-- One row per seat per day. The authoritative counter lives in Redis for
-- speed; this table is reconciled nightly so a cache loss cannot permanently
-- hand out free allowance.
-- ---------------------------------------------------------------------------
CREATE TABLE agent_usage_daily (
    workspace_id  UUID    NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id       UUID    NOT NULL REFERENCES users(id)      ON DELETE CASCADE,
    usage_date    DATE    NOT NULL,
    questions     INTEGER NOT NULL DEFAULT 0 CHECK (questions >= 0),
    tool_calls    INTEGER NOT NULL DEFAULT 0 CHECK (tool_calls >= 0),
    refusals      INTEGER NOT NULL DEFAULT 0 CHECK (refusals >= 0),
    blocked       INTEGER NOT NULL DEFAULT 0 CHECK (blocked >= 0),
    p95_latency_ms INTEGER,
    PRIMARY KEY (workspace_id, user_id, usage_date)
);

CREATE INDEX agent_usage_workspace_day_idx
    ON agent_usage_daily (workspace_id, usage_date DESC);

-- ---------------------------------------------------------------------------
-- Sessions
-- The unit of conversational context, and the unit an administrator revokes.
-- Ending a session does not end the DemandIQ session.
-- ---------------------------------------------------------------------------
CREATE TABLE agent_sessions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id      UUID NOT NULL REFERENCES users(id)      ON DELETE CASCADE,
    -- Location scope the conversation was pinned to, NULL for all locations.
    location_id  UUID REFERENCES locations(id) ON DELETE SET NULL,
    device       TEXT,
    ip_hash      TEXT,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at     TIMESTAMPTZ,
    end_reason   TEXT CHECK (end_reason IN
                   ('user', 'idle_timeout', 'max_duration', 'turn_limit',
                    'admin_revoked', 'signed_out')),
    turn_count   INTEGER NOT NULL DEFAULT 0 CHECK (turn_count >= 0),
    CONSTRAINT ended_after_started CHECK (ended_at IS NULL OR ended_at >= started_at)
);

-- Live-session lookups drive both the admin table and the concurrency check.
CREATE INDEX agent_sessions_live_idx
    ON agent_sessions (workspace_id, last_seen_at DESC)
    WHERE ended_at IS NULL;

-- ---------------------------------------------------------------------------
-- Turns
-- The transcript. answer_json stores the structured answer the UI renders,
-- including its sources, so an answer shown months later is the answer that
-- was actually given.
-- ---------------------------------------------------------------------------
CREATE TABLE agent_turns (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id       UUID NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    question         TEXT NOT NULL,
    answer_json      JSONB,
    tools_used       TEXT[] NOT NULL DEFAULT '{}',
    -- Source ids the answer cited: forecast ids, model versions, job run ids.
    source_refs      TEXT[] NOT NULL DEFAULT '{}',
    refused          BOOLEAN NOT NULL DEFAULT FALSE,
    guardrail_blocks TEXT[] NOT NULL DEFAULT '{}',
    latency_ms       INTEGER,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- An answer that was neither refused nor blocked must cite something.
    CONSTRAINT answer_is_sourced CHECK (
        refused
        OR cardinality(guardrail_blocks) > 0
        OR cardinality(source_refs) > 0
    )
);

CREATE INDEX agent_turns_session_idx ON agent_turns (session_id, created_at);

-- ---------------------------------------------------------------------------
-- Saved answers
-- ---------------------------------------------------------------------------
CREATE TABLE agent_pins (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES users(id)        ON DELETE CASCADE,
    turn_id    UUID NOT NULL REFERENCES agent_turns(id)  ON DELETE CASCADE,
    note       TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, turn_id)
);

-- ---------------------------------------------------------------------------
-- Guardrails
-- Locked rules carry the product's honesty claims and cannot be disabled by
-- an administrator. The database enforces that, not just the application.
-- ---------------------------------------------------------------------------
CREATE TABLE agent_guardrails (
    id           TEXT PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    label        TEXT    NOT NULL,
    detail       TEXT    NOT NULL,
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    locked       BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT locked_rules_stay_on CHECK (NOT locked OR enabled)
);

-- ---------------------------------------------------------------------------
-- Retention
-- Transcripts expire on a schedule; usage aggregates are kept for billing.
-- ---------------------------------------------------------------------------
-- DELETE FROM agent_turns
--  WHERE created_at < now() - INTERVAL '90 days';
