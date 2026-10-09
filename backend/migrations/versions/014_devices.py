"""Device fleet — enrolled devices, grants, events, and consent-gated mirroring.

Adds four tables for the device ecosystem. The reasoning lives in
``app/models/device.py``; this file is the DDL that realises it.

Splitting these four tables, rather than one ``devices`` table with a JSONB
blob, is the decision worth stating: they have different write patterns
(``device_events`` is append-only and high-volume, ``devices`` is read-mostly),
different retention needs, and — for ``mirror_sessions`` — a different
*authority* model. A permission that a second human must approve should not be
able to share a row-shape with a heartbeat.

``mirror_sessions`` carries the two-person rule as a table CHECK, the same way
``change_requests`` does. The service layer also refuses self-approval, but a
CHECK is the thing that survives a future migration, a direct ``psql`` session,
or a background job that decides sessions without going through the router.

``devices`` reserves ``public_key`` for hardware-backed attestation. It is NULL
in every row today, because a platform key store is not something a restaurant
tablet can be assumed to expose. Reserving the column now makes adding
attestation a backfill instead of a migration on a table that will hold live
customer credentials.

Revision ID: 014_devices
Revises: 013_session_origin
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "014_devices"
down_revision = "013_session_origin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "devices",
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
        # SET NULL, not CASCADE: losing a location must not erase the record
        # that a device was enrolled against it.
        sa.Column(
            "location_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("locations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("device_type", sa.Text(), nullable=False, server_default="pos"),
        sa.Column("platform", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("app_version", sa.Text(), nullable=True),
        sa.Column("credential_prefix", sa.Text(), nullable=False),
        sa.Column("credential_hash", sa.Text(), nullable=False),
        sa.Column(
            "scopes",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("public_key", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "enrolled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "enrolled_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mirroring_consent_at", sa.DateTime(timezone=True), nullable=True),
        # The credential prefix is how a device is found before its hash is
        # checked, so it has to be unique per workspace rather than globally.
        sa.UniqueConstraint(
            "workspace_id", "credential_prefix", name="uq_devices_workspace_prefix"
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked', 'quarantined')", name="ck_devices_status"
        ),
    )
    op.create_index("ix_devices_workspace_id", "devices", ["workspace_id"])
    op.create_index("ix_devices_location_id", "devices", ["location_id"])

    op.create_table(
        "device_grants",
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
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column(
            "granted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # NULL = never expires. Expiry is enforced by comparing to now(), never
        # by deleting the row: an expired grant is the evidence that a
        # permission once existed.
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "revoked_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
    )
    op.create_index("ix_device_grants_workspace_id", "device_grants", ["workspace_id"])
    op.create_index("ix_device_grants_device_id", "device_grants", ["device_id"])
    op.create_index(
        "ix_device_grants_device_capability", "device_grants", ["device_id", "capability"]
    )

    op.create_table(
        "device_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_device_events_workspace_id", "device_events", ["workspace_id"])
    op.create_index("ix_device_events_device_id", "device_events", ["device_id"])
    op.create_index(
        "ix_device_events_device_recorded", "device_events", ["device_id", "recorded_at"]
    )

    op.create_table(
        "mirror_sessions",
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
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "decided_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        # The two-person rule, as a database invariant. The service layer also
        # refuses self-approval; this is what makes it true regardless of who
        # writes the row. Mirrors ``change_requests.two_person_rule``.
        sa.CheckConstraint(
            "decided_by IS NULL OR decided_by <> requested_by", name="two_person_rule"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'ended', 'denied')", name="ck_mirror_sessions_status"
        ),
    )
    op.create_index("ix_mirror_sessions_workspace_id", "mirror_sessions", ["workspace_id"])
    op.create_index("ix_mirror_sessions_device_id", "mirror_sessions", ["device_id"])
    op.create_index("ix_mirror_sessions_device_status", "mirror_sessions", ["device_id", "status"])


def downgrade() -> None:
    # Reverse dependency order: sessions and events reference devices, so they
    # go first. Dropping ``devices`` before its children would fail on the FKs.
    op.drop_table("mirror_sessions")
    op.drop_table("device_events")
    op.drop_table("device_grants")
    op.drop_table("devices")
