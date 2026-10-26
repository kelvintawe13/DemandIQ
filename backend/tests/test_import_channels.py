"""The channels imports arrive through.

``GET /sources`` is a view over ``connectors``, and only a connector pull used to
tag its rows. A workspace that imported everything by hand therefore had a full
demand series and no channel to show for it: the screen said "No sources
connected" over data it was holding. The registry here is what gives an import a
channel, so it is pinned directly — the mocked-session suite cannot see the row
it writes.

The real round trip (an import, then ``GET /sources``) lives in
``tests/test_import_channels_integration.py``, which needs a migrated Postgres.
"""

from __future__ import annotations

import datetime
import uuid

from app.models.location import Connector
from app.services.ingestion import channels


class _StubSession:
    """Answers ``scalar`` from a script and records what else it was asked."""

    def __init__(self, *replies: object) -> None:
        self._replies = list(replies)
        self.scalar_calls = 0
        self.inserts: list[object] = []
        self.flushed = 0

    def scalar(self, stmt: object) -> object:
        self.scalar_calls += 1
        return self._replies.pop(0) if self._replies else None

    def execute(self, stmt: object) -> None:
        self.inserts.append(stmt)

    def flush(self) -> None:
        self.flushed += 1


def _connector(ws: uuid.UUID, *, type_: str, name: str) -> Connector:
    return Connector(
        id=uuid.uuid4(),
        workspace_id=ws,
        type=type_,
        name=name,
        status="available",
        credentials={"import_kind": type_},
        last_sync_at=None,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def test_every_import_kind_has_a_named_channel() -> None:
    """The four sources ``commit_demand`` is called with by the routes."""
    assert channels.spec_for("file") == channels.ImportChannel(
        source="file", name="Spreadsheet uploads", type="file"
    )
    assert channels.spec_for("image") == channels.ImportChannel(
        source="image", name="Photo uploads", type="ocr"
    )
    assert channels.spec_for("manual") == channels.ImportChannel(
        source="manual", name="Manual entry", type="manual"
    )
    assert channels.spec_for("device") == channels.ImportChannel(
        source="device", name="Till devices", type="pos"
    )


def test_unknown_import_kind_registers_nothing() -> None:
    """An unrecognised tag gets no channel rather than an invented one.

    A channel nobody can explain is worse than an untagged row: the Sources
    screen would list a source that does not exist.
    """
    session = _StubSession()

    assert channels.channel_for_source(session, uuid.uuid4(), "carrier-pigeon") is None
    assert session.scalar_calls == 0
    assert session.inserts == []


def test_existing_channel_is_reused_without_a_second_insert() -> None:
    ws = uuid.uuid4()
    existing = _connector(ws, type_="manual", name="Manual entry")
    session = _StubSession(existing)

    found = channels.channel_for_source(session, ws, "manual")

    assert found is existing
    assert session.inserts == []


def test_first_use_creates_the_channel() -> None:
    """No channel yet: insert one, then return the row that landed.

    ``scalar`` is called twice — before the insert to look for an existing
    channel, after it to read back what the database kept.
    """
    ws = uuid.uuid4()
    created = _connector(ws, type_="file", name="Spreadsheet uploads")
    session = _StubSession(None, created)

    found = channels.channel_for_source(session, ws, "file")

    assert found is created
    assert len(session.inserts) == 1
    # ``ON CONFLICT DO NOTHING`` on the *partial* index, so a concurrent import
    # cannot create the channel twice. The predicate is part of the conflict
    # target: without it Postgres has no matching unique index and refuses the
    # statement outright.
    statement = str(session.inserts[0])
    assert "ON CONFLICT (workspace_id, type, name)" in statement
    assert "credentials ? 'import_kind'" in statement
    assert "DO NOTHING" in statement


def test_a_mocked_session_does_not_masquerade_as_a_channel() -> None:
    """A MagicMock answers every query; it must not become a ``source_id``."""
    session = _StubSession(object(), object())

    assert channels.channel_for_source(session, uuid.uuid4(), "file") is None


def test_record_import_counts_what_the_import_did() -> None:
    ws = uuid.uuid4()
    channel = _connector(ws, type_="file", name="Spreadsheet uploads")
    session = _StubSession()

    channels.record_import(
        session,
        channel,
        committed=5,
        updated=2,
        skipped=3,
        detail="demand.csv: 5 new, 2 corrected",
    )

    # Read back, a person asked "what did this channel deliver?": seven rows were
    # written (five of them new), three were left behind, and the detail says
    # which is which.
    assert channel.last_pull_committed == 7
    assert channel.last_pull_pulled == 10
    assert channel.last_pull_skipped == 3
    assert channel.last_pull_detail == "demand.csv: 5 new, 2 corrected"
    assert channel.last_sync_at is not None
    assert session.flushed == 1


def test_record_import_stamps_the_moment_it_was_given() -> None:
    """Freshness is the import's clock, not the wall clock at test time."""
    when = datetime.datetime(2026, 3, 1, 9, 30, tzinfo=datetime.UTC)
    channel = _connector(uuid.uuid4(), type_="manual", name="Manual entry")

    channels.record_import(_StubSession(), channel, committed=1, when=when)

    assert channel.last_sync_at == when
