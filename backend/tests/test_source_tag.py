"""Provenance: which channel wrote a demand row, and where to read it back.

Every channel funnels through :func:`commit_demand`, which records its ``source``
in the row's notes so the row can be attributed without a JOIN. The writer and
the reader did not agree on the format — the writer emitted a bare ``source:file``
when the row had no operator note, and ``GET /ingest/demand`` only parsed the
bracketed ``[source:file]`` — so most rows in a workspace reported
``"source": null``. Found by driving all five channels for real; pinned here.
"""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.location import DemandObservation
from app.models.user import User, UserRole
from app.models.workspace import Workspace
from app.routers.ingest import _source_tag


def test_tag_without_notes_is_bracketed() -> None:
    assert _source_tag("[source:device]") == "device"


def test_tag_with_operator_notes_is_still_found() -> None:
    assert _source_tag("promo night [source:manual]") == "manual"


def test_legacy_bare_tag_is_read() -> None:
    """Rows written before the formats agreed still report their channel."""
    assert _source_tag("source:file") == "file"


def test_untagged_notes_report_nothing() -> None:
    assert _source_tag("Checked by Alice") is None
    assert _source_tag(None) is None
    # A word that merely ends in "source" is not a tag.
    assert _source_tag("mysource:file") is None


def _make_workspace() -> Workspace:
    return Workspace(
        id=uuid.uuid4(),
        name="Test Co",
        business_type="cafe",
        country="RW",
        timezone="Africa/Kigali",
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _make_user(ws_id: uuid.UUID) -> User:
    return User(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        name="Test User",
        email="test@example.com",
        password_hash="$2b$12$dummy",
        role=UserRole.owner,
        email_verified=True,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def test_demand_list_reports_the_channel_for_a_legacy_row() -> None:
    """The read path is where the tag was lost, so it is exercised end to end."""
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc_id = uuid.uuid4()
    row = DemandObservation(
        id=1,
        workspace_id=ws.id,
        location_id=loc_id,
        date=datetime.date(2026, 8, 1),
        demand_qty=41,
        source_id=None,
        upload_id=None,
        notes="source:file",
        recorded_at=datetime.datetime.now(datetime.UTC),
    )

    db = MagicMock()
    db.scalars.return_value = MagicMock(all=MagicMock(return_value=[row]))

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user

    resp = TestClient(app).get("/api/v1/ingest/demand")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"][0]["source"] == "file"
