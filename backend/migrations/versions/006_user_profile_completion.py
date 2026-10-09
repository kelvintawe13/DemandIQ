"""Profile completion gate — users.profile_completed

Revision ID: 006_user_profile_completion
Revises: 005_agent_governance
Create Date: 2026-09-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "006_user_profile_completion"
down_revision: str | None = "005_agent_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("profile_completed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Existing account holders (email/password) completed a profile at signup;
    # only fresh OAuth provisions need the completion gate.
    op.execute("UPDATE users SET profile_completed = true")


def downgrade() -> None:
    op.drop_column("users", "profile_completed")
