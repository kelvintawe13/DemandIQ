"""Persist the signup profile on the workspace.

``POST /auth/register`` has always ACCEPTED ``location_count``,
``daily_covers`` and ``data_sources[]`` — the sign-up form has asked for
them since the first release — but the workspace row had nowhere to put
them, so they were parsed, validated and then silently dropped. Every
downstream surface then had to guess what the organisation told us
outright: the first-location form cannot prefill, and "how many sites do
you run?" is unanswerable from the database.

Nullable columns: only new sign-ups supply them, and back-filling guesses
would violate the rule that nothing in this schema invents a value.

Revision ID: 019_workspace_signup_profile
Revises: 018_dark_theme_default
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "019_workspace_signup_profile"
down_revision = "018_dark_theme_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workspaces", sa.Column("location_count", sa.Integer(), nullable=True))
    op.add_column("workspaces", sa.Column("daily_covers", sa.Integer(), nullable=True))
    op.add_column("workspaces", sa.Column("data_sources", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("workspaces", "data_sources")
    op.drop_column("workspaces", "daily_covers")
    op.drop_column("workspaces", "location_count")
