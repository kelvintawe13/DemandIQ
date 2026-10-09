"""Site administration: platform_settings table.

Key/value store for site-wide configuration managed by the Site Admin
console (registration open/closed, default workspace seat, announcement
banner, support contacts, feature flags). JSONB values keep each setting
self-describing; ``updated_at`` + ``updated_by`` give every change an owner.

Revision ID: 008_site_admin
Revises: 007_schema_drift_backfill
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "008_site_admin"
down_revision = "007_schema_drift_backfill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_settings",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("platform_settings")
