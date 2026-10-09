"""Agent governance tables — metering, quotas, transcripts, guardrails.

Brings database/AGENT_SCHEMA.sql into the alembic migration chain so that
alembic-migrated databases (not just docker-initialized ones) can serve the
assistant endpoints without 500s. Also seeds the three default quota tiers.

Revision ID: 005_agent_governance
Revises: 004_serving_state
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005_agent_governance"
down_revision: str | None = "004_serving_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_quota_tiers",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("daily_questions", sa.Integer(), nullable=False),
        sa.Column("monthly_questions", sa.Integer(), nullable=False),
        sa.Column("concurrent_sessions", sa.SmallInteger(), nullable=False),
        sa.Column("allowed_tools", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.CheckConstraint("daily_questions > 0", name="ck_agent_tiers_daily_positive"),
        sa.CheckConstraint("monthly_questions > 0", name="ck_agent_tiers_monthly_positive"),
        sa.CheckConstraint(
            "concurrent_sessions BETWEEN 1 AND 10", name="ck_agent_tiers_concurrency_range"
        ),
        sa.CheckConstraint(
            "monthly_questions >= daily_questions", name="ck_agent_tiers_monthly_at_least_daily"
        ),
    )

    op.create_table(
        "agent_seat_quotas",
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tier_id",
            sa.Text(),
            sa.ForeignKey("agent_quota_tiers.id"),
            nullable=False,
        ),
        sa.Column("daily_override", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column(
            "updated_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("daily_override > 0", name="ck_agent_seat_daily_override_positive"),
        sa.PrimaryKeyConstraint("workspace_id", "user_id"),
    )
    op.create_check_constraint(
        "ck_agent_seat_status",
        "agent_seat_quotas",
        "status IN ('active', 'throttled', 'suspended')",
    )

    op.create_table(
        "agent_quota_grants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("scope_role", sa.Text(), nullable=True),
        sa.Column("extra_daily", sa.Integer(), nullable=False),
        sa.Column(
            "issued_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("extra_daily > 0", name="ck_agent_grants_extra_positive"),
        sa.CheckConstraint(
            "user_id IS NOT NULL OR scope_role IS NOT NULL", name="ck_agent_grants_targets_someone"
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_agent_grants_expires_later"),
    )
    op.create_index(
        "agent_grants_active_idx",
        "agent_quota_grants",
        ["workspace_id", sa.text("expires_at DESC")],
    )

    op.create_table(
        "agent_usage_daily",
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("questions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("refusals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("p95_latency_ms", sa.Integer(), nullable=True),
        sa.CheckConstraint("questions >= 0", name="ck_agent_usage_questions_nonneg"),
        sa.CheckConstraint("tool_calls >= 0", name="ck_agent_usage_tools_nonneg"),
        sa.CheckConstraint("refusals >= 0", name="ck_agent_usage_refusals_nonneg"),
        sa.CheckConstraint("blocked >= 0", name="ck_agent_usage_blocked_nonneg"),
        sa.PrimaryKeyConstraint("workspace_id", "user_id", "usage_date"),
    )
    op.create_index(
        "agent_usage_workspace_day_idx",
        "agent_usage_daily",
        ["workspace_id", sa.text("usage_date DESC")],
    )

    op.create_table(
        "agent_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "location_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("device", sa.Text(), nullable=True),
        sa.Column("ip_hash", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.Text(), nullable=True),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("turn_count >= 0", name="ck_agent_sessions_turn_count_nonneg"),
        sa.CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at",
            name="ck_agent_sessions_ended_after_started",
        ),
    )
    op.create_check_constraint(
        "ck_agent_sessions_end_reason",
        "agent_sessions",
        "end_reason IN ('user', 'idle_timeout', 'max_duration', 'turn_limit', "
        "'admin_revoked', 'signed_out')",
    )
    op.create_index(
        "agent_sessions_live_idx",
        "agent_sessions",
        ["workspace_id", sa.text("last_seen_at DESC")],
        postgresql_where=sa.text("ended_at IS NULL"),
    )

    op.create_table(
        "agent_turns",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer_json", postgresql.JSONB(), nullable=True),
        sa.Column("tools_used", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("source_refs", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("refused", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "guardrail_blocks", postgresql.ARRAY(sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "refused OR cardinality(guardrail_blocks) > 0 OR cardinality(source_refs) > 0",
            name="ck_agent_turns_answer_is_sourced",
        ),
    )
    op.create_index("agent_turns_session_idx", "agent_turns", ["session_id", "created_at"])

    op.create_table(
        "agent_pins",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "turn_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_turns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "turn_id", name="uq_agent_pins_user_turn"),
    )

    op.create_table(
        "agent_guardrails",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.CheckConstraint(
            "NOT locked OR enabled", name="ck_agent_guardrails_locked_rules_stay_on"
        ),
    )

    # Default quota tiers (the app's seat assignment defaults to tier 'operator').
    op.execute("""
        INSERT INTO agent_quota_tiers (id, name, daily_questions, monthly_questions,
                                       concurrent_sessions, allowed_tools, description)
        VALUES
          ('operator', 'Operator', 20, 400, 2,
           ARRAY['forecast', 'quality', 'alerts', 'reports'],
           'Front-of-house and kitchen staff: forecasts, quality and alerts.'),
          ('analyst', 'Analyst', 50, 1000, 3,
           ARRAY['forecast', 'quality', 'alerts', 'reports', 'analytics', 'policies'],
           'Analysts: everything operators get plus analytics and policy simulation.'),
          ('administrator', 'Administrator', 100, 2000, 5,
           ARRAY['forecast', 'quality', 'alerts', 'reports', 'analytics', 'policies',
                 'models', 'experiments', 'datasets', 'connectors'],
           'Administrators: full tool surface, highest allowances.')
        ON CONFLICT (id) DO NOTHING
        """)


def downgrade() -> None:
    op.drop_table("agent_guardrails")
    op.drop_table("agent_pins")
    op.drop_index("agent_turns_session_idx", table_name="agent_turns")
    op.drop_table("agent_turns")
    op.drop_index("agent_sessions_live_idx", table_name="agent_sessions")
    op.drop_table("agent_sessions")
    op.drop_index("agent_usage_workspace_day_idx", table_name="agent_usage_daily")
    op.drop_table("agent_usage_daily")
    op.drop_index("agent_grants_active_idx", table_name="agent_quota_grants")
    op.drop_table("agent_quota_grants")
    op.drop_table("agent_seat_quotas")
    op.drop_table("agent_quota_tiers")
