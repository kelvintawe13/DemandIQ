"""Record each connector's most recent pull outcome.

``connectors.last_sync_at`` says *when* a channel last pulled, but nothing said
*what happened*: how many rows the provider returned, how many were committed,
and how many were skipped because they could not be mapped. The Sources page
could therefore only show a timestamp, and the comment there had to admit that a
per-source filter over ``pipeline_runs`` was impossible because the sweep records
no connector id.

These four nullable columns close that gap. They are written by the
``connector_sync`` job and by the on-demand ``POST /connectors/{id}/pull``, and
read back on ``GET /sources``:

* ``last_pull_pulled``    — rows the provider returned
* ``last_pull_committed`` — rows written/reconciled through ``commit_demand``
* ``last_pull_skipped``   — rows that failed mapping/validation
* ``last_pull_detail``    — the run's one-line summary (and, for a channel with
  no endpoint, the reason no pull ran)

They stay ``NULL`` until a pull has run, so "never pulled" is distinguishable
from "pulled zero rows". Nullable rather than defaulted to ``0`` for exactly
that reason: a fabricated zero would read as a successful empty pull.

Revision ID: 016_connector_pull_outcome
Revises: 015_demand_storage_range
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "016_connector_pull_outcome"
down_revision = "015_demand_storage_range"
branch_labels = None
depends_on = None

COLUMNS: list[tuple[str, sa.types.TypeEngine]] = [
    ("last_pull_pulled", sa.Integer()),
    ("last_pull_committed", sa.Integer()),
    ("last_pull_skipped", sa.Integer()),
    ("last_pull_detail", sa.Text()),
]


def upgrade() -> None:
    for name, type_ in COLUMNS:
        op.add_column("connectors", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name, _type in reversed(COLUMNS):
        op.drop_column("connectors", name)
