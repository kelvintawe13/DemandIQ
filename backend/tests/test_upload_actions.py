"""The actions on a staged upload: undo the import, and read it again.

Both exist for the same failure: an import that wrote the wrong numbers. Before
them, a committed upload was undeletable and there was nothing on screen to do
about it, so a bad OCR read became permanent history.
"""

from __future__ import annotations

import base64
import datetime
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.location import IngestUpload
from app.models.user import User, UserRole
from app.models.workspace import Workspace

_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
)


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


def _make_upload(
    ws_id: uuid.UUID,
    *,
    kind: str = "image",
    status: str = "ready",
    source: bytes | None = _ONE_PIXEL_PNG,
    row_count: int = 3,
) -> IngestUpload:
    return IngestUpload(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        uploaded_by=None,
        kind=kind,
        filename="log.png",
        status=status,
        row_count=row_count,
        error_count=0,
        preview={"headers": ["date", "demand_qty"], "rows": []},
        source_image=source,
        source_mime="image/png" if source else None,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _client(ws: Workspace, user: User, db: MagicMock) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _db_for(upload: IngestUpload, *, removed: int = 0) -> MagicMock:
    db = MagicMock()
    db.get.side_effect = lambda model, pk: upload if pk == upload.id else None
    db.execute.return_value = MagicMock(rowcount=removed)
    db.flush = MagicMock()
    db.delete = MagicMock()
    return db


# ------------------------------------------------------------------ undo ---


def test_deleting_a_committed_import_removes_the_rows_it_wrote() -> None:
    """Undo is the whole import, and the report says how much history went."""
    ws = _make_workspace()
    user = _make_user(ws.id)
    upload = _make_upload(ws.id, status="committed", row_count=21)
    db = _db_for(upload, removed=21)

    client = _client(ws, user, db)
    resp = client.delete(f"/api/v1/ingest/uploads/{upload.id}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"deleted": True, "filename": "log.png", "removed_rows": 21}
    # The observations are deleted with the upload, not orphaned behind it.
    assert db.execute.called
    db.delete.assert_called_once_with(upload)


def test_deleting_a_staged_import_reports_nothing_removed() -> None:
    """An uncommitted upload wrote no rows, so the count is honestly zero."""
    ws = _make_workspace()
    user = _make_user(ws.id)
    upload = _make_upload(ws.id, status="ready", row_count=3)
    db = _db_for(upload, removed=0)

    client = _client(ws, user, db)
    resp = client.delete(f"/api/v1/ingest/uploads/{upload.id}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["removed_rows"] == 0


def test_deleting_another_workspaces_import_is_refused() -> None:
    other = _make_workspace()
    user = _make_user(other.id)
    upload = _make_upload(uuid.uuid4(), status="committed")
    db = _db_for(upload, removed=5)

    client = _client(other, user, db)
    resp = client.delete(f"/api/v1/ingest/uploads/{upload.id}")

    assert resp.status_code == 404
    db.delete.assert_not_called()


# ----------------------------------------------------------------- retry ---


def test_retry_reads_the_stored_original_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """A staged photo can be re-read without uploading it a second time."""
    ws = _make_workspace()
    user = _make_user(ws.id)
    upload = _make_upload(ws.id, status="failed", row_count=0)
    # The page was read under a stated reporting year; the retry has to reuse it.
    upload.preview = {"rows": [], "reporting_year": 2024}
    db = _db_for(upload)

    seen: list[tuple[bytes, str, int | None]] = []

    def fake_preview(data: bytes, filename: str, **kwargs: object) -> dict:
        reporting_year = kwargs.get("reporting_year")
        seen.append((data, filename, reporting_year if isinstance(reporting_year, int) else None))
        return {
            "headers": ["date", "demand_qty"],
            "rows": [{"date": "2026-10-01", "demand_qty": 58}],
            "row_count": 21,
            "engine": "tesseract",
        }

    monkeypatch.setattr("app.routers.ingest.preview_for_image", fake_preview)

    client = _client(ws, user, db)
    resp = client.post(f"/api/v1/ingest/uploads/{upload.id}/retry")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "job_id": str(upload.id),
        "row_count": 21,
        "engine": "tesseract",
        "status": "ready",
    }
    # The bytes it re-read are the ones that were stored, not a new upload — and
    # the year the operator chose, not the year it happens to be today.
    assert seen == [(_ONE_PIXEL_PNG, "log.png", 2024)]
    assert upload.row_count == 21
    assert upload.status == "ready"
    assert upload.error_count == 0


def test_retry_refuses_when_nothing_was_stored() -> None:
    """A spreadsheet keeps its parsed rows, not the sheet: say so plainly."""
    ws = _make_workspace()
    user = _make_user(ws.id)
    upload = _make_upload(ws.id, kind="csv", source=None, status="failed")

    client = _client(ws, user, _db_for(upload))
    resp = client.post(f"/api/v1/ingest/uploads/{upload.id}/retry")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "no_source"


def test_retry_refuses_a_committed_import() -> None:
    """Its rows are history; re-reading would stage a second set behind them."""
    ws = _make_workspace()
    user = _make_user(ws.id)
    upload = _make_upload(ws.id, status="committed", row_count=21)

    client = _client(ws, user, _db_for(upload))
    resp = client.post(f"/api/v1/ingest/uploads/{upload.id}/retry")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "already_committed"
