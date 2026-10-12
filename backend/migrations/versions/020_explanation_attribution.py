"""Record which path wrote an explanation row.

``forecast_explanations`` is read as evidence about a forecast, but the row said
nothing about where its numbers came from, and the two sources are not
interchangeable:

* the served booster's own TreeSHAP, computed for the vector the point was
  scored from; and
* the placeholder used when no bundle is loaded, which is a deterministic
  function of the feature *name* and carries no information about demand.

A deployment with no bundle writes the second kind, and nothing in the data
distinguished it from the first — an operator reading "these pushed the number
up" had no way to learn that no model had been consulted. The value is stored
per row (not inferred at read time) because the numbers alone cannot tell the
two apart, and because the answer must not change if a bundle is installed
later: the row describes the run that wrote it.

Back-fill is ``stub`` rather than NULL: before this migration the placeholder was
the only writer, so that is a true statement about every existing row, not a
guess. Rows written from now on set the value explicitly.

Revision ID: 020_explanation_attribution
Revises: 019_workspace_signup_profile
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Kept inside 32 characters: alembic's own ``alembic_version.version_num`` is
# ``varchar(32)``, so a longer id fails on the UPDATE that records the upgrade
# — after the DDL in this file has been attempted. Verified against a scratch
# database, not by inspection.
revision = "020_explanation_attribution"
down_revision = "019_workspace_signup_profile"
branch_labels = None
depends_on = None

# The suffix, not the full name: these ops run through the metadata naming
# convention (``ck_%(table_name)s_%(constraint_name)s``), so the name reaches the
# database already prefixed. Passing the full name here prefixes it twice, and a
# doubled name past 63 characters is truncated by Postgres with a hash suffix —
# leaving the database disagreeing with the model about a constraint the model
# declares. The middleware name is ``ck_forecast_explanations_attribution_source``
# (verified on a scratch database, both after upgrade and after downgrade).
_CONSTRAINT = "attribution_source"
_ALLOWED = "attribution_source IN ('booster', 'stub')"


def upgrade() -> None:
    op.add_column(
        "forecast_explanations",
        sa.Column("attribution_source", sa.Text(), nullable=False, server_default="stub"),
    )
    op.create_check_constraint(_CONSTRAINT, "forecast_explanations", _ALLOWED)


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "forecast_explanations", type_="check")
    op.drop_column("forecast_explanations", "attribution_source")
