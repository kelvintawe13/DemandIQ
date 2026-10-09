"""Site administrator flag — users.is_site_admin.

A site admin manages the *platform* (all workspaces, users, settings) —
distinct from a workspace owner who manages one workspace's data. The
``admin@demandiq.app`` seeded account is the only holder by default.

Revision ID: 009_site_admin_flag
Revises: 008_site_admin
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "009_site_admin_flag"
down_revision = "008_site_admin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_site_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # The seeded platform-admin account is the default holder.
    op.execute("UPDATE users SET is_site_admin = true WHERE email = 'admin@demandiq.app'")


def downgrade() -> None:
    op.drop_column("users", "is_site_admin")
