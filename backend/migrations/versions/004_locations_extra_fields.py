"""Locations — extra fields for comprehensive site management.

Revision ID: 004_locations_extra_fields
Revises: 003_billing
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004_locations_extra_fields"
down_revision: str | None = "003_billing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add extra descriptive / operational fields to locations.
    # All nullable for backward compatibility — existing rows stay valid.
    op.add_column("locations", sa.Column("seating_capacity", sa.Integer(), nullable=True))
    op.add_column("locations", sa.Column("service_type", sa.Text(), nullable=True))
    op.add_column("locations", sa.Column("district", sa.Text(), nullable=True))
    op.add_column("locations", sa.Column("open_days_mask", sa.Text(), nullable=True))
    op.add_column("locations", sa.Column("address", sa.Text(), nullable=True))
    op.add_column("locations", sa.Column("phone", sa.Text(), nullable=True))
    op.add_column("locations", sa.Column("manager_name", sa.Text(), nullable=True))
    op.add_column("locations", sa.Column("notes", sa.Text(), nullable=True))
    # Helpful index for district filtering
    op.create_index("ix_locations_district", "locations", ["district"])


def downgrade() -> None:
    op.drop_index("ix_locations_district", table_name="locations")
    op.drop_column("locations", "notes")
    op.drop_column("locations", "manager_name")
    op.drop_column("locations", "phone")
    op.drop_column("locations", "address")
    op.drop_column("locations", "open_days_mask")
    op.drop_column("locations", "district")
    op.drop_column("locations", "service_type")
    op.drop_column("locations", "seating_capacity")
