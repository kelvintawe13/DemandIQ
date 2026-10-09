"""Add ``experiments.created_at``.

The ``Experiment`` ORM model has declared ``created_at`` (and orders the list by
it) since the table was first written, but the column was never created — every
``SELECT`` the model generated asked for a column the database did not have.
``GET /api/v1/experiments`` caught that ``ProgrammingError`` and returned an
empty list, so the Experiments screen showed its empty state no matter how many
rows existed, and ``POST /api/v1/experiments`` would have failed on insert. The
schema now matches the model, with the same ``now()`` default every other
``created_at`` in this database carries.

Revision ID: 017_experiment_created_at
Revises: 016_connector_pull_outcome
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "017_experiment_created_at"
down_revision = "016_connector_pull_outcome"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_column("experiments", "created_at")
