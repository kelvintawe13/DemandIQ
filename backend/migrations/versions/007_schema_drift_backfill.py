"""Schema-drift backfill — self-heal deployments that ran older migrations.

Two columns exist in the current models (and in fresh migration runs) but are
missing on databases migrated before the historical migrations were extended:

* ``reservations.reserved_visitors`` — added to the Reservation model for the
  ``rsv_*`` serving features (001 was edited after some deployments ran it).
* ``calibrations.aci_state`` — the online conformal state column (004).

Both are additive and nullable, so this backfill is safe and idempotent
(``ADD COLUMN IF NOT EXISTS``) — it also upgrades any environment whose schema
predates the model changes without touching any data.

Revision ID: 007_schema_drift_backfill
Revises: 006_user_profile_completion
"""

from __future__ import annotations

from alembic import op

revision = "007_schema_drift_backfill"
down_revision = "006_user_profile_completion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE reservations ADD COLUMN IF NOT EXISTS reserved_visitors INTEGER")
    op.execute("ALTER TABLE calibrations ADD COLUMN IF NOT EXISTS aci_state JSONB")


def downgrade() -> None:
    # Columns are additive and model-mandated; drift backfill has no downgrade.
    pass
