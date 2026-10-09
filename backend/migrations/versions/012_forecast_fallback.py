"""Forecast provenance — forecasts.fallback + a distinct fallback model status.

Two related changes.

1. ``forecasts`` recorded the point/interval and the model version, but not
   *whether the value came from a model at all*. Callers had to guess:

   * ``GET /forecasts`` hardcoded ``fallback: false`` on every row (a comment
     noted it "cannot [be] store[d] directly without schema change");
   * ``GET /forecasts/{id}`` recomputed it by rebuilding the feature vector at
     *request* time against the row's *historical* ``as_of_timestamp`` — an
     answer derived from data the forecast never saw.

   The flag is a property of the forecast, so it is stored with it. ``true``
   means the served point is a historical average rather than a model output:
   the location had fewer than 7 days of demand history in the lookback window.
   Existing rows default to ``false`` — a past forecast's history is not
   recoverable, and inventing one is the bug this column exists to stop.

2. ``model_status`` gains ``fallback``. The degraded serving paths return a
   rolling mean, and that used to be filed against whatever row had
   ``status='production'`` — crediting a trained LightGBM run with a number it
   did not produce. Rows previously created for this purpose (version
   ``v0-fallback``) are re-labelled from ``production`` to ``fallback`` so the
   fix applies to existing databases too.

``ALTER TYPE ... ADD VALUE`` cannot be *used* in the transaction that adds it,
so it runs in an autocommit block and the re-labelling follows after the commit.

Revision ID: 012_forecast_fallback
Revises: 011_risk_aware_policy
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "012_forecast_fallback"
down_revision = "011_risk_aware_policy"
branch_labels = None
depends_on = None

# The registry row the loader creates for forecasts no model produced.
# Kept in step with ``app.services.forecasting.loader.STUB_MODEL_VERSION``.
_STUB_MODEL_VERSION = "v0-fallback"


def upgrade() -> None:
    op.add_column(
        "forecasts",
        sa.Column("fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Postgres refuses to use a new enum member in the transaction that created
    # it, so the ADD VALUE must commit on its own before the UPDATE below.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE model_status ADD VALUE IF NOT EXISTS 'fallback'")
    # Self-heal databases where the loader already created the row as
    # `production` (the old, dishonest label).
    op.execute(
        sa.text(
            "UPDATE model_versions SET status = 'fallback' WHERE version = :version"
        ).bindparams(version=_STUB_MODEL_VERSION)
    )


def downgrade() -> None:
    # Restore the old label first: the column is what we can actually drop, and
    # Postgres cannot remove a value from an enum.
    op.execute(
        sa.text(
            "UPDATE model_versions SET status = 'production' WHERE version = :version"
        ).bindparams(version=_STUB_MODEL_VERSION)
    )
    op.drop_column("forecasts", "fallback")
