"""Merge heads — rejoin the locations branch with the main migration line.

``004_locations_extra_fields`` branched off ``003_billing`` while the main line
continued to ``009_site_admin_flag``. Two heads make ``alembic upgrade head``
fail with "Multiple head revisions are present", which breaks fresh deploys and
the auth E2E (and therefore ``make verify``). This no-op revision joins them so
``head`` is a single, unambiguous target again.

Deliberately a merge (not a re-parent of ``004_locations_extra_fields``) so that
environments already stamped at either branch stay valid.

Revision ID: 010_merge_heads
Revises: 009_site_admin_flag, 004_locations_extra_fields
Create Date: 2026-09-22

"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "010_merge_heads"
down_revision: str | Sequence[str] | None = (
    "009_site_admin_flag",
    "004_locations_extra_fields",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No DDL — this revision only rejoins the migration graph."""


def downgrade() -> None:
    """No DDL — downgrading re-splits the graph back into two heads."""
