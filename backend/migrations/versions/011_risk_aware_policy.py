"""Risk-aware preparation policy — Section 3.17.

Section 3.17 permits "an additional risk-aware policy ... to balance potential
surplus exposure and unmet-demand exposure". It is a distinct rule (a newsvendor
quantity derived from an explicit cost ratio), not a second coverage level, so
the database needs two things: the enum value that lets a
``preparation_policies`` row carry the kind at all, and a column for the cost
ratio that defines it.

Both statements run in an autocommit block: ``ALTER TYPE ... ADD VALUE`` cannot
run inside a transaction block on PostgreSQL 11 and earlier, and on 12+ the new
value is not visible to statements in the same transaction. The autocommit block
is Alembic's documented idiom for exactly this case, so the ADD VALUE commits
before anything reads it.

Revision ID: 011_risk_aware_policy
Revises: 010_merge_heads
Create Date: 2026-09-22

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "011_risk_aware_policy"
down_revision: str | Sequence[str] | None = "010_merge_heads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the risk_aware policy kind and its cost-ratio parameter."""
    with op.get_context().autocommit_block():
        op.execute(
            "DO $$ BEGIN "
            "ALTER TYPE policy_kind ADD VALUE IF NOT EXISTS 'risk_aware'; "
            "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
        )
    op.execute(
        "ALTER TABLE preparation_policies " "ADD COLUMN IF NOT EXISTS risk_weight NUMERIC(4,2)"
    )


def downgrade() -> None:
    """Drop the parameter column.

    PostgreSQL cannot remove a value from an existing enum type, and dropping
    and recreating ``policy_kind`` would rewrite every dependent column. So the
    column goes and the enum value is left in place; existing rows keep a value
    the application no longer offers, which is inert rather than harmful.
    """
    op.execute("ALTER TABLE preparation_policies DROP COLUMN IF EXISTS risk_weight")
