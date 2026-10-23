"""Keeping the original photo with the upload that read it.

Image ingestion used to keep only the parsed rows, so the review step ("check
each row against the photo") could not actually be done once the upload
finished: the photo lived in the browser tab, and was gone on reload. These
tests pin the replacement — the bytes are stored on the upload, served back only
to its own workspace, and listed as a flag rather than as bytes.

The database is mocked, matching ``test_ingest.py``: what is under test here is
the route contract (what is stored, what is served, who may read it), not SQL.
"""

from __future__ import annotations

import base64
import datetime
import io
import uuid
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.location import IngestUpload
from app.models.user import User, UserRole
from app.models.workspace import Workspace

# A real 1x1 PNG: the upload route sniffs the bytes, so a placeholder string is
# (correctly) rejected before it ever reaches storage.
_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
)


def _make_workspace(name: str = "Test Co") -> Workspace:
    return Workspace(
        id=uuid.uuid4(),
        name=name,
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


def _make_upload(
    ws_id: uuid.UUID,
    *,
    kind: str = "image",
    filename: str = "log.png",
    source: bytes | None = _ONE_PIXEL_PNG,
    source_mime: str | None = "image/png",
    status: str = "ready",
) -> IngestUpload:
    return IngestUpload(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        uploaded_by=None,
        kind=kind,
        filename=filename,
        status=status,
        row_count=1,
        error_count=0,
        preview={"headers": ["date", "demand_qty"], "rows": []},
        source_image=source,
        source_mime=source_mime,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _client(ws: Workspace, user: User, db: MagicMock) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_image_upload_keeps_the_original_bytes() -> None:
    """The photo is stored on the upload, with the type sniffed from the bytes."""
    ws = _make_workspace()
    user = _make_user(ws.id)
    stored: list[IngestUpload] = []

    db = MagicMock()
    db.add.side_effect = lambda obj: stored.append(obj) if isinstance(obj, IngestUpload) else None
    db.flush = MagicMock()

    client = _client(ws, user, db)
    resp = client.post(
        "/api/v1/ingest/image",
        files={"file": ("log.png", io.BytesIO(_ONE_PIXEL_PNG), "image/png")},
    )
    assert resp.status_code == 201, resp.text

    assert len(stored) == 1
    upload = stored[0]
    assert upload.source_image == _ONE_PIXEL_PNG
    # From the sniffed format, not the multipart part's declared type.
    assert upload.source_mime == "image/png"


def test_source_endpoint_serves_the_stored_photo() -> None:
    """GET .../source returns the bytes inline, typed from what was stored."""
    ws = _make_workspace()
    user = _make_user(ws.id)
    upload = _make_upload(ws.id)

    db = MagicMock()
    db.get.side_effect = lambda model, pk: upload if pk == upload.id else None

    client = _client(ws, user, db)
    resp = client.get(f"/api/v1/ingest/uploads/{upload.id}/source")

    assert resp.status_code == 200, resp.text
    assert resp.content == _ONE_PIXEL_PNG
    assert resp.headers["content-type"] == "image/png"
    # Rendered by the browser, not downloaded — it is there to be looked at.
    assert resp.headers["content-disposition"].startswith("inline;")
    assert "log.png" in resp.headers["content-disposition"]
    # Workspace data: no shared cache, and no content sniffing of a user upload.
    assert resp.headers["cache-control"].startswith("private")
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_source_endpoint_hides_other_workspaces_photos() -> None:
    """A photo is as private as the rows in it: another workspace gets 404."""
    other_ws = _make_workspace("Someone Else")
    user = _make_user(other_ws.id)
    upload = _make_upload(uuid.uuid4())  # belongs to a different workspace

    db = MagicMock()
    db.get.side_effect = lambda model, pk: upload if pk == upload.id else None

    client = _client(other_ws, user, db)
    resp = client.get(f"/api/v1/ingest/uploads/{upload.id}/source")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_source_endpoint_says_so_when_nothing_was_kept() -> None:
    """An upload from before photos were kept has no original — that is a 404.

    Returning an empty 200 would render as a broken image, which reads as a
    failure of the photo rather than an upload that never had one stored.
    """
    ws = _make_workspace()
    user = _make_user(ws.id)
    upload = _make_upload(ws.id, kind="csv", filename="demand.csv", source=None, source_mime=None)

    db = MagicMock()
    db.get.side_effect = lambda model, pk: upload if pk == upload.id else None

    client = _client(ws, user, db)
    resp = client.get(f"/api/v1/ingest/uploads/{upload.id}/source")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "no_source"


def test_uploads_list_flags_the_photo_without_sending_it() -> None:
    """The list answers "can this be shown?" as a boolean, never as bytes."""
    ws = _make_workspace()
    user = _make_user(ws.id)
    with_photo = _make_upload(ws.id, filename="log.png")
    without = _make_upload(ws.id, filename="demand.csv", kind="csv", source=None, source_mime=None)

    db = MagicMock()
    # Exactly what the route's SELECT returns: the row plus the SQL flag. The
    # deferred bytes are not part of it, which is the point.
    db.execute.return_value = MagicMock(
        all=MagicMock(return_value=[(with_photo, True), (without, False)])
    )

    client = _client(ws, user, db)
    resp = client.get("/api/v1/ingest/uploads")

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert [row["has_source"] for row in data] == [True, False]
    assert all(isinstance(row["has_source"], bool) for row in data)


def test_source_image_is_deferred_on_the_model() -> None:
    """A page of uploads must not pull megabytes of photos into the session.

    Without ``deferred`` the list query loads every stored photo just to render
    filenames; the flag in the list response is computed in SQL instead. This
    pins the mapping property the whole design depends on.
    """
    assert IngestUpload.__mapper__.attrs["source_image"].deferred is True
