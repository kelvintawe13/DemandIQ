"""Default the platform theme to dark.

``user_preferences.theme`` was created with ``DEFAULT 'light'`` (002). The
platform now opens in dark mode, so the column default has to agree with the
model (``UserPreference.theme``) and the API fallback (``GET /me/preferences``)
— three places that would otherwise disagree the moment a row is inserted by
anything other than the ORM.

This changes the default only. Existing rows keep whatever the user chose: a
stored ``'light'`` is an explicit preference and must not be rewritten by a
default swap.

Revision ID: 018_dark_theme_default
Revises: 017_experiment_created_at
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "018_dark_theme_default"
down_revision = "017_experiment_created_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "user_preferences",
        "theme",
        server_default=sa.text("'dark'"),
        existing_type=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "user_preferences",
        "theme",
        server_default=sa.text("'light'"),
        existing_type=sa.Text(),
        existing_nullable=False,
    )
