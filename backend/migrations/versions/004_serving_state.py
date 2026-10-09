"""Serving state — reservation party-size sums and live online-ACI state.

Revision ID: 004_serving_state
Revises: 003_billing
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004_serving_state"
down_revision: str | None = "003_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Party-size sum booked before the visit day (rsv_visitors feature).
    op.add_column("reservations", sa.Column("reserved_visitors", sa.Integer(), nullable=True))
    # Live online-ACI state (serving-calibrated alphas + trailing residual
    # windows per stratum, plus the global fallback), updated as realised
    # demand arrives.
    op.add_column("calibrations", sa.Column("aci_state", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("calibrations", "aci_state")
    op.drop_column("reservations", "reserved_visitors")
