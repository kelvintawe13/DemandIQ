"""Register the channels that imports arrive through.

``GET /sources`` is a view over ``connectors``. Only a connector pull tagged its
rows, so a workspace whose entire history came in by spreadsheet, photo, hand
entry or till push had ``source_id`` NULL on every observation and the Sources
page reported "No sources connected" over a full demand series — the channel that
brought the data in was not a thing the schema could name.

This migration creates those channels for the imports that already happened, and
tags their rows:

======================  ======  ===========================
``[source:...]`` tag    type    channel name
======================  ======  ===========================
``file``                file    Spreadsheet uploads
``image``               ocr     Photo uploads
``manual``              manual  Manual entry
``device``              pos     Till devices
======================  ======  ===========================

``last_sync_at`` and the ``last_pull_*`` counts are filled from the rows each
channel owns, so the first thing Sources says about a backfilled channel is true
rather than empty: the newest row's ``recorded_at`` is when it last received
data, and the row count is what it delivered.

``connector_type`` gains ``manual``: hand entry is a real channel and none of the
existing values names it (``api`` would claim an integration that does not
exist). ``ALTER TYPE ... ADD VALUE`` cannot be used in the transaction that adds
it, so it runs in an autocommit block before the backfill, as migration 012 does.

The unique index is *partial* — only rows carrying ``import_kind`` — so it
constrains the channels this code creates and nothing an administrator typed in.
Without it two simultaneous imports could create the same channel twice.

Downgrade removes the channels and the tags it wrote. It cannot remove the enum
value: Postgres has no ``DROP VALUE``, and rewriting the type would fail while
any row still used it.

Revision ID: 023_import_channels
Revises: 022_report_content
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "023_import_channels"
down_revision = "022_report_content"
branch_labels = None
depends_on = None

#: (observation tag, connector type, channel name, backfill detail)
_CHANNELS: tuple[tuple[str, str, str, str], ...] = (
    ("file", "file", "Spreadsheet uploads", "backfilled from existing spreadsheet imports"),
    ("image", "ocr", "Photo uploads", "backfilled from existing photo imports"),
    ("manual", "manual", "Manual entry", "backfilled from existing manual entries"),
    ("device", "pos", "Till devices", "backfilled from existing till pushes"),
)

_INDEX = "uq_connectors_import_channel"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'manual'")

    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX} "
        "ON connectors (workspace_id, type, name) WHERE (credentials ? 'import_kind')"
    )

    for source, ctype, name, detail in _CHANNELS:
        tag = f"[source:{source}]"
        # One channel per workspace that actually has rows from this source. A
        # workspace that never uploaded a spreadsheet gets no spreadsheet
        # channel: an empty one would be a channel invented to fill a screen.
        # Every value is bound, including the connector type: the f-string form
        # tripped ruff's SQL-injection check, and a parameter is the honest fix
        # (the enum is cast explicitly because a bare parameter in an INSERT ...
        # SELECT has no type for Postgres to infer it from).
        op.execute(
            sa.text(
                "INSERT INTO connectors "
                "(id, workspace_id, type, name, status, credentials, created_at) "
                "SELECT gen_random_uuid(), d.workspace_id, CAST(:ctype AS connector_type), "
                ":name, 'available', jsonb_build_object('import_kind', :kind), now() "
                "FROM demand_observations d "
                "WHERE d.notes LIKE :tag "
                "GROUP BY d.workspace_id "
                "ON CONFLICT DO NOTHING"
            ).bindparams(ctype=ctype, name=name, kind=source, tag=f"%{tag}%")
        )
        # Tag only untagged rows: a row already attributed to a connector pull
        # keeps that attribution, which is the more specific fact.
        op.execute(
            sa.text(
                "UPDATE demand_observations d SET source_id = c.id "
                "FROM connectors c "
                "WHERE c.workspace_id = d.workspace_id "
                "AND c.type = CAST(:ctype AS connector_type) AND c.name = :name "
                "AND d.source_id IS NULL AND d.notes LIKE :tag"
            ).bindparams(ctype=ctype, name=name, tag=f"%{tag}%")
        )

    # Fill the channel's own counters from the rows it now owns.
    op.execute(
        sa.text(
            "UPDATE connectors c SET "
            "last_sync_at = s.max_at, last_pull_pulled = s.n, last_pull_committed = s.n, "
            "last_pull_skipped = 0, last_pull_detail = :detail "
            "FROM (SELECT source_id, count(*) AS n, max(recorded_at) AS max_at "
            "      FROM demand_observations WHERE source_id IS NOT NULL "
            "      GROUP BY source_id) s "
            "WHERE c.id = s.source_id AND c.credentials ? 'import_kind'"
        ).bindparams(detail="row count backfilled from existing data")
    )


def downgrade() -> None:
    op.execute(
        "UPDATE demand_observations SET source_id = NULL "
        "WHERE source_id IN (SELECT id FROM connectors WHERE credentials ? 'import_kind')"
    )
    op.execute("DELETE FROM connectors WHERE credentials ? 'import_kind'")
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
