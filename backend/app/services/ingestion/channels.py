"""The channels a workspace imports through, as rows in ``connectors``.

``GET /sources`` is a view over ``connectors``: status, freshness, volume and the
last pull, per channel. Only a connector pull ever tagged its rows, though, so a
workspace whose entire history arrived by spreadsheet, photo or hand entry had
``source_id`` NULL on every observation and the Sources page truthfully reported
"No sources connected" over a full demand series. The data was there; the channel
that brought it in did not exist as a thing the schema could name.

One channel per import kind, created on first use and reused afterwards:

===========  =====================  ======
``source``   channel name           type
===========  =====================  ======
``file``     Spreadsheet uploads    file
``image``    Photo uploads          ocr
``manual``   Manual entry           manual
``device``   Till devices           pos
===========  =====================  ======

They are created ``available``, never ``active``. ``_job_connector_sync`` pulls
every active connector from the endpoint in its credentials, and an import
channel has no endpoint to pull from — active would make each sync run log a
channel that cannot answer, and ``POST /sources/{id}/sync`` would offer a pull
that does nothing. ``available`` is the status the schema already gives a
connector with nothing configured.

``credentials`` carries ``{"import_kind": <source>}`` so these rows stay
distinguishable from a connector an administrator typed in by hand with the same
name, and so the migration that backfills them can find exactly what it created.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.location import Connector

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ImportChannel:
    """How one import kind appears as a channel."""

    source: str
    name: str
    type: str


IMPORT_CHANNELS: dict[str, ImportChannel] = {
    "file": ImportChannel(source="file", name="Spreadsheet uploads", type="file"),
    "image": ImportChannel(source="image", name="Photo uploads", type="ocr"),
    "manual": ImportChannel(source="manual", name="Manual entry", type="manual"),
    "device": ImportChannel(source="device", name="Till devices", type="pos"),
}


def spec_for(source: str) -> ImportChannel | None:
    """The channel spec for an import kind, or ``None`` for an unknown one.

    ``None`` rather than a default: an unrecognised source tag means this
    workspace's rows came from something nothing here knows how to name, and
    inventing a channel for it would put a fiction on the Sources page.
    """
    return IMPORT_CHANNELS.get(source)


def channel_for_source(db: Session, workspace_id: uuid.UUID, source: str) -> Connector | None:
    """Return this workspace's channel for ``source``, creating it on first use."""
    spec = spec_for(source)
    if spec is None:
        return None

    query = select(Connector).where(
        Connector.workspace_id == workspace_id,
        Connector.type == spec.type,
        Connector.name == spec.name,
    )
    # ``isinstance`` rather than truthiness: a mocked session answers with a
    # MagicMock, and treating that as a channel would tag real callers with a
    # fake id. Same reason ``app.jobs.runner._orm`` filters by class.
    existing = db.scalar(query)
    if isinstance(existing, Connector):
        return existing

    # ``ON CONFLICT DO NOTHING`` rather than a bare INSERT: two imports can land
    # together, and the unique index on (workspace_id, type, name) makes the
    # second a no-op instead of a duplicate channel or a failed import.
    db.execute(
        pg_insert(Connector).values(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            type=spec.type,
            name=spec.name,
            status="available",
            credentials={"import_kind": source},
            last_sync_at=None,
            created_at=datetime.datetime.now(datetime.UTC),
        )
        # Matches the *partial* unique index from migration 023: it covers the
        # channels this module creates and leaves an administrator's connectors
        # (which carry no ``import_kind``) unconstrained by it.
        .on_conflict_do_nothing(
            index_elements=["workspace_id", "type", "name"],
            index_where=text("credentials ? 'import_kind'"),
        )
    )
    created = db.scalar(query)
    created = created if isinstance(created, Connector) else None
    if created is None:  # pragma: no cover - would need the unique index to vanish
        logger.warning(
            "import channel insert did not land workspace=%s source=%s", workspace_id, source
        )
    return created


def record_import(
    db: Session,
    channel: Connector,
    *,
    committed: int,
    updated: int = 0,
    skipped: int = 0,
    detail: str | None = None,
    when: datetime.datetime | None = None,
) -> None:
    """Stamp what an import did on its channel.

    ``committed`` is rows written for the first time; ``updated`` is rows the
    import reconciled onto an existing (location, date). Both are kept because
    "9 new, 3 corrected" and "12 new" are different facts about a channel, and
    the Sources page shows the total it can defend: every row this import
    committed, with the split in ``detail``.
    """
    channel.last_sync_at = when or datetime.datetime.now(datetime.UTC)
    channel.last_pull_pulled = committed + updated + skipped
    channel.last_pull_committed = committed + updated
    channel.last_pull_skipped = skipped
    channel.last_pull_detail = detail
    db.flush()
