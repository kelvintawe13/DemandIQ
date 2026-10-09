"""Widen demand-unit numeric columns to match the range demand can actually take.

``demand_observations.demand_qty`` is an ``integer``: a location may record any
quantity up to 2,147,483,647. The columns that store *results computed from*
that demand were sized for a different assumption — ``forecasts.point_forecast``
and its two bounds were ``numeric(10, 2)``, which tops out at 99,999,999.99.

The mismatch was not theoretical. A single manual entry of 222,000,100 was
accepted by every ingest path (the only rule was ``>= 0``), stored, and then
detonated every later ``POST /forecasts`` for that location as a Postgres
``NumericValueOutOfRange`` surfacing as a 500 — the operator's typing looked
fine and an unrelated page broke. Ingest now refuses a quantity that cannot be
served back (``normaliser.MAX_DEMAND_QTY``), and this migration removes the
storage half of the problem so the two cannot disagree again.

``numeric(14, 2)`` leaves twelve integer digits: the full ``integer`` range plus
two digits of headroom for the interval margin a point forecast is widened by.

The same audit turned up two neighbours carrying demand-unit values, which are
fixed here rather than left to fail one table later:

* ``forecast_explanations.shap_impact`` — per-feature attribution, in demand
  units, so its magnitude tracks the forecast. Was ``numeric(10, 3)``.
* ``calibrations.mean_interval_width`` — a width in demand units, so a demand of
  500,000 produces a width in the hundreds of thousands. Was ``numeric(8, 3)``,
  whose ceiling is 99,999.999: a legitimate five-figure daily demand would have
  broken the calibration write on the ingest request itself.

Deliberately **not** widened: ``model_versions.mae``/``rmse``. They also carry
demand units, but they are written from a training run's export manifest rather
than from a request, and no user-entered number reaches them. If the modelling
path is ever pointed at demand this large, that is a separate decision with its
own evaluation to redo.

``policy_simulations``' four ``numeric(12, 2)`` outcomes are already wide enough
for the whole ``integer`` range and are left alone.

Revision ID: 015_demand_storage_range
Revises: 014_devices
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "015_demand_storage_range"
down_revision = "014_devices"
branch_labels = None
depends_on = None

# (table, column, old type, new type) — one list, so the downgrade cannot
# disagree with the upgrade about what changed.
WIDENED: list[tuple[str, str, sa.Numeric, sa.Numeric, bool]] = [
    ("forecasts", "point_forecast", sa.Numeric(10, 2), sa.Numeric(14, 2), False),
    ("forecasts", "lower_bound", sa.Numeric(10, 2), sa.Numeric(14, 2), False),
    ("forecasts", "upper_bound", sa.Numeric(10, 2), sa.Numeric(14, 2), False),
    ("forecast_explanations", "shap_impact", sa.Numeric(10, 3), sa.Numeric(14, 3), False),
    # The only nullable one of the five: a calibration can exist before it has
    # been scored against a realised day.
    ("calibrations", "mean_interval_width", sa.Numeric(8, 3), sa.Numeric(14, 3), True),
]


def _alter(table: str, column: str, old: sa.Numeric, new: sa.Numeric, nullable: bool) -> None:
    op.alter_column(
        table,
        column,
        existing_type=old,
        type_=new,
        existing_nullable=nullable,
    )


def upgrade() -> None:
    for table, column, old, new, nullable in WIDENED:
        _alter(table, column, old, new, nullable)


def downgrade() -> None:
    # Narrowing is lossy, and Postgres will refuse rather than round: if any
    # row now holds a value outside the old precision the ``ALTER`` raises
    # ``numeric field overflow``. That is the right failure — silently
    # truncating a customer's stored forecast would be worse — but it means this
    # downgrade is only safe on data that was written before this migration.
    for table, column, old, new, nullable in reversed(WIDENED):
        _alter(table, column, new, old, nullable)
