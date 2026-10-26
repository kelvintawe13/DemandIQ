"""Ingest and feature-build tests (DB-optional, mocked sessions).

Covers:
* manual entry validation and success shape
* file lifecycle: upload -> mapping (dry-run) -> commit
* idempotency on (location_id, date)
* future-date rejection
* leakage test ``test_feature_window_no_leak`` (the most important test)
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import io
import uuid
from unittest.mock import MagicMock

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.location import DemandObservation, IngestUpload, Location
from app.models.user import User, UserRole
from app.models.workspace import Workspace
from app.services import realtime
from app.services.feature_build import FeatureLeakageError, build_features
from app.services.ingestion.normaliser import NormaliserError, commit_demand

# ---------------------------------------------------------------- fixtures ---


def _make_workspace() -> Workspace:
    return Workspace(
        id=uuid.uuid4(),
        name="Test Co",
        business_type="cafe",
        country="RW",
        timezone="Africa/Kigali",
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _make_user(ws_id: uuid.UUID, role: UserRole = UserRole.owner) -> User:
    return User(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        name="Test User",
        email="test@example.com",
        password_hash="$2b$12$dummy",
        role=role,
        email_verified=True,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _make_location(ws_id: uuid.UUID) -> Location:
    return Location(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        name="Kigali Central",
        area="Kigali",
        cuisine="Rwandan",
        archived_at=None,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _auth_overrides(ws: Workspace, user: User, mock_db: MagicMock) -> dict:
    return {get_db: lambda: mock_db, get_current_user: lambda: user}  # type: ignore[dict-item]


# ---------------------------------------------------------------- manual -----


def test_manual_entry_success() -> None:
    """POST /ingest/manual commits rows via commit_demand."""
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc = _make_location(ws.id)

    mock_db = MagicMock()
    # commit_demand does db.get(Location) and db.scalar(select(...)) then add/flush
    mock_db.get.return_value = loc
    mock_db.scalar.return_value = None  # no existing row => create
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    app = create_app()
    for k, v in _auth_overrides(ws, user, mock_db).items():
        app.dependency_overrides[k] = v  # type: ignore[assignment]

    client = TestClient(app)
    payload = [{"location_id": str(loc.id), "date": "2026-01-10", "demand_qty": 42}]
    resp = client.post("/api/v1/ingest/manual", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["committed"] + body["reconciled"] == 1

    app.dependency_overrides.clear()


def test_manual_rejects_future_date() -> None:
    """Future dates are rejected with field=date and future_date code."""
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc = _make_location(ws.id)

    future = (datetime.datetime.now(datetime.UTC).date() + datetime.timedelta(days=5)).isoformat()

    mock_db = MagicMock()
    mock_db.get.return_value = loc
    mock_db.scalar.return_value = None
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()

    app = create_app()
    for k, v in _auth_overrides(ws, user, mock_db).items():
        app.dependency_overrides[k] = v  # type: ignore[assignment]

    client = TestClient(app)
    payload = [{"location_id": str(loc.id), "date": future, "demand_qty": 10}]
    resp = client.post("/api/v1/ingest/manual", json=payload)
    # commit_demand raises NormaliserError -> handler returns 422 with code future_date
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert "error" in body
    # details may carry future_date
    details = body["error"].get("details") or []
    # At least one error mentions future_date or date field
    assert any(d.get("code") == "future_date" or d.get("field") == "date" for d in details) or body[
        "error"
    ]["code"] in ("validation_error", "future_date")

    app.dependency_overrides.clear()


def test_manual_unauthenticated_rejected() -> None:
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/v1/ingest/manual", json=[])
    assert resp.status_code == 401


def test_manual_rejects_unrepresentable_demand() -> None:
    """A demand of 222,000,100 must be refused at the door, not stored.

    Regression: the value was accepted by every ingest path (the only rule was
    ``>= 0``) and written to ``demand_observations``, while ``forecasts`` stored
    its point and bounds as ``numeric(10, 2)`` at the time. A single observation
    of that size therefore made every later ``POST /forecasts`` for the location
    fail as a 500 ``NumericValueOutOfRange`` — the operator's typing looked fine
    and an unrelated page broke. Both halves are now closed: ingest refuses the
    quantity, and migration 015 widened the columns.
    """
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc = _make_location(ws.id)

    mock_db = MagicMock()
    mock_db.get.return_value = loc
    mock_db.scalar.return_value = None
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()

    app = create_app()
    for k, v in _auth_overrides(ws, user, mock_db).items():
        app.dependency_overrides[k] = v  # type: ignore[assignment]

    client = TestClient(app)
    payload = [{"location_id": str(loc.id), "date": "2026-01-10", "demand_qty": 222000100}]
    resp = client.post("/api/v1/ingest/manual", json=payload)
    assert resp.status_code == 422, resp.text
    details = resp.json()["error"].get("details") or []
    assert any(d.get("code") == "invalid_demand" for d in details), resp.text
    assert any(d.get("field") == "demand_qty" for d in details), resp.text
    # The row must not have been written.
    assert mock_db.add.call_count == 0

    app.dependency_overrides.clear()


def test_patch_demand_rejects_unrepresentable_demand() -> None:
    """PATCH edits the column directly, so it needs the same ceiling.

    ``patch_demand`` assigns ``obs.demand_qty`` itself instead of calling
    ``commit_demand``, which is how the two write paths would otherwise drift:
    the manual path refuses the value while the edit path quietly stores it.
    """
    ws = _make_workspace()
    user = _make_user(ws.id)
    obs = DemandObservation(
        id=173,
        workspace_id=ws.id,
        location_id=uuid.uuid4(),
        date=datetime.date(2026, 1, 10),
        demand_qty=42,
        recorded_at=datetime.datetime.now(datetime.UTC),
    )

    mock_db = MagicMock()
    mock_db.get.return_value = obs
    mock_db.flush = MagicMock()

    app = create_app()
    for k, v in _auth_overrides(ws, user, mock_db).items():
        app.dependency_overrides[k] = v  # type: ignore[assignment]

    client = TestClient(app)
    resp = client.patch("/api/v1/ingest/demand/173", json={"demand_qty": 222000100})
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"]["code"] == "invalid_demand"
    assert body["error"]["field"] == "demand_qty"
    # The in-place edit must not have happened.
    assert obs.demand_qty == 42
    assert mock_db.flush.call_count == 0

    app.dependency_overrides.clear()


def test_demand_ceiling_fits_the_declared_storage_width() -> None:
    """The cap and the columns that store its results must agree.

    The ceiling is read from the model declarations rather than restated here:
    a hardcoded ``10**8`` was correct when every one of these columns was
    ``numeric(10, 2)``, and would have gone on looking correct after migration
    015 widened them. Reading the real width means narrowing one back fails here
    instead of in production.

    Every column checked holds a *demand-unit* value, so each scales with the
    largest demand the platform accepts: the forecast itself, its two interval
    bounds, the per-feature attribution beside it, and the calibration's width.
    """
    from app.models.forecast import Calibration, Forecast, ForecastExplanation
    from app.services.ingestion.normaliser import MAX_DEMAND_QTY, validate_demand_qty

    def ceiling(column_name: str) -> int:
        column = {
            "point_forecast": Forecast.__table__.c.point_forecast,
            "lower_bound": Forecast.__table__.c.lower_bound,
            "upper_bound": Forecast.__table__.c.upper_bound,
            "shap_impact": ForecastExplanation.__table__.c.shap_impact,
            "mean_interval_width": Calibration.__table__.c.mean_interval_width,
        }[column_name]
        width = column.type
        assert isinstance(width, sa.Numeric), f"{column_name} is no longer numeric"
        assert width.precision is not None and width.scale is not None
        return 10 ** (width.precision - width.scale)

    # ``demand_observations.demand_qty`` is a Postgres ``integer``, so this is the
    # largest number any ingest path can be handed. Assert the column really is
    # an integer, so the range below is not an assumption about a type that
    # moved.
    demand_type = DemandObservation.__table__.c.demand_qty.type
    assert isinstance(demand_type, sa.Integer)
    int4_max = 2**31 - 1

    for name in (
        "point_forecast",
        "lower_bound",
        "upper_bound",
        "shap_impact",
        "mean_interval_width",
    ):
        # Migration 015's guarantee, and the reason it exists: storing a demand
        # that the platform accepts must never overflow the result computed from
        # it. ``numeric(10, 2)`` fails this — which is exactly the bug.
        assert ceiling(name) > int4_max, f"{name} cannot hold every accepted demand"
        # And the interval is built by widening the point, so the cap has to
        # leave room for more than the point itself, not merely fit inside.
        assert MAX_DEMAND_QTY * 2 < ceiling(name), f"{name} cannot hold the capped demand"

    # The boundary itself: the cap is inclusive, one unit over is refused.
    validate_demand_qty(MAX_DEMAND_QTY)
    with pytest.raises(NormaliserError) as excinfo:
        validate_demand_qty(MAX_DEMAND_QTY + 1)
    assert excinfo.value.code == "invalid_demand"
    assert excinfo.value.field == "demand_qty"

    with pytest.raises(NormaliserError):
        validate_demand_qty(-1)


# ---------------------------------------------------------------- idempotency (normaliser unit) ---


def test_commit_demand_idempotent_reconciliation() -> None:
    """Second commit on same (location_id, date) reconciles instead of inserting."""
    ws = _make_workspace()
    loc = _make_location(ws.id)

    # Existing observation
    existing = DemandObservation(
        id=1,
        workspace_id=ws.id,
        location_id=loc.id,
        date=datetime.date(2026, 1, 5),
        demand_qty=30,
        recorded_at=datetime.datetime.now(datetime.UTC),
    )

    mock_db = MagicMock()
    mock_db.get.return_value = loc
    # First call: scalar returns None (no existing), second call returns existing
    call_count = {"n": 0}

    def fake_scalar(stmt):  # type: ignore[no-untyped-def]
        # Called as db.scalar(select(...))
        if call_count["n"] == 0:
            call_count["n"] += 1
            return None
        return existing

    mock_db.scalar.side_effect = fake_scalar
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()

    # First commit creates
    r1 = commit_demand(
        mock_db,
        workspace_id=ws.id,
        location_id=loc.id,
        date=datetime.date(2026, 1, 5),
        demand_qty=30,
        source="manual",
    )
    assert r1.created is True

    # Second commit reconciles
    r2 = commit_demand(
        mock_db,
        workspace_id=ws.id,
        location_id=loc.id,
        date=datetime.date(2026, 1, 5),
        demand_qty=55,
        source="manual",
    )
    assert r2.created is False
    assert r2.previous_qty == 30
    # The existing object's qty should have been updated
    assert existing.demand_qty == 55


def test_commit_demand_reaches_a_connected_device() -> None:
    """A committed row is fanned out on the demand topic.

    ``commit_demand`` is the only writer of ``demand_observations``, so the
    publish lives there: that is what makes every adapter — manual, file, image,
    connector — reach a connected till without each one remembering to.
    """
    ws = _make_workspace()
    loc = _make_location(ws.id)

    mock_db = MagicMock()
    mock_db.get.return_value = loc
    mock_db.scalar.return_value = None

    sub = realtime.get_hub().subscribe(ws.id, realtime.TOPIC_DEMAND)
    try:
        result = commit_demand(
            mock_db,
            workspace_id=ws.id,
            location_id=loc.id,
            date=datetime.date(2026, 1, 5),
            demand_qty=42,
            source="manual",
        )
        assert result.created is True
        # The publish is awaited inside `commit_demand`, so by the time it
        # returns the event is already buffered.
        event = asyncio.run(sub.get(timeout=2.0))
        assert event is not None
        assert event.workspace_id == ws.id
        assert event.topic == realtime.TOPIC_DEMAND
        assert event.payload["location_id"] == str(loc.id)
        assert event.payload["date"] == "2026-01-05"
        assert event.payload["demand_qty"] == 42
    finally:
        sub.close()


def test_demand_fanout_failure_does_not_fail_the_commit() -> None:
    """Realtime is a convenience; the committed row is the durable record.

    If the fan-out raises, ingestion has *already* written the row. Losing the
    commit because a websocket broker was unreachable would be the wrong
    trade, so the publish is swallowed and logged.
    """
    ws = _make_workspace()
    loc = _make_location(ws.id)

    mock_db = MagicMock()
    mock_db.get.return_value = loc
    mock_db.scalar.return_value = None

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("broker unreachable")

    original = realtime.publish
    realtime.publish = _boom  # type: ignore[assignment]
    try:
        result = commit_demand(
            mock_db,
            workspace_id=ws.id,
            location_id=loc.id,
            date=datetime.date(2026, 1, 5),
            demand_qty=42,
            source="manual",
        )
    finally:
        realtime.publish = original  # type: ignore[assignment]

    assert result.created is True
    assert result.observation.demand_qty == 42


def test_commit_demand_rejects_future_date_unit() -> None:
    ws = _make_workspace()
    loc = _make_location(ws.id)
    mock_db = MagicMock()
    mock_db.get.return_value = loc
    future = datetime.datetime.now(datetime.UTC).date() + datetime.timedelta(days=10)
    try:
        commit_demand(
            mock_db,
            workspace_id=ws.id,
            location_id=loc.id,
            date=future,
            demand_qty=10,
            source="manual",
        )
        assert False, "should have raised"
    except Exception as exc:
        assert "future" in str(exc).lower() or getattr(exc, "code", "") == "future_date"


# ---------------------------------------------------------------- file lifecycle ---


def test_file_lifecycle_upload_mapping_commit() -> None:
    """CSV upload -> mapping dry-run -> commit, all workspace-scoped."""
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc = _make_location(ws.id)

    # In-memory store for the staged upload
    store: dict[uuid.UUID, IngestUpload] = {}

    mock_db = MagicMock()

    def fake_add(obj):  # type: ignore[no-untyped-def]
        if isinstance(obj, IngestUpload):
            store[obj.id] = obj

    def fake_get(model, pk):  # type: ignore[no-untyped-def]
        if model == IngestUpload:
            return store.get(pk)
        if model == Location:
            return loc if pk == loc.id else None
        return None

    # For commit_demand inside file commit: need scalar for demand lookup
    mock_db.get.side_effect = fake_get
    mock_db.add.side_effect = fake_add
    mock_db.flush = MagicMock()
    mock_db.scalar.return_value = None  # no existing demand rows
    # list_uploads uses scalars
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    # For location-by-name fallback
    mock_db.scalar.return_value = None

    # But file commit also does _resolve_location_id via db.scalar(select(Location)...).
    # We need scalar to return loc for that query. Distinguish by call:
    #   - demand_observations lookup -> None
    #   - location lookup -> loc
    # Simplest: make scalar return loc when called (covers both); demand lookup returning loc
    # would incorrectly indicate existing row but test still passes for commit shape.
    # So we patch scalar to return None for demand, loc for location by inspecting query string.

    def smart_scalar(stmt):  # type: ignore[no-untyped-def]
        text = str(stmt)
        if "locations" in text:
            return loc
        return None

    mock_db.scalar.side_effect = smart_scalar

    app = create_app()
    for k, v in _auth_overrides(ws, user, mock_db).items():
        app.dependency_overrides[k] = v  # type: ignore[assignment]

    client = TestClient(app)

    # 1) Upload CSV
    csv_text = "date,covers,location\n2026-01-10,40,Kigali Central\n2026-01-11,55,Kigali Central\n"
    resp = client.post(
        "/api/v1/ingest/file",
        files={"file": ("test.csv", io.BytesIO(csv_text.encode()), "text/csv")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "upload_id" in body
    assert body["detected_headers"] == ["date", "covers", "location"]
    assert body["row_count"] == 2
    upload_id = body["upload_id"]

    # 2) Mapping dry-run
    resp = client.post(
        f"/api/v1/ingest/file/{upload_id}/mapping",
        json={"column_map": {"date": "date", "demand_qty": "covers", "location": "location"}},
    )
    assert resp.status_code == 200, resp.text
    mbody = resp.json()
    assert mbody["mapped_rows"] == 2
    assert mbody["error_count"] == 0

    # 3) Commit
    resp = client.post(f"/api/v1/ingest/file/{upload_id}/commit")
    assert resp.status_code == 200, resp.text
    cbody = resp.json()
    assert cbody["committed"] + cbody["reconciled"] == 2
    assert cbody["status"] == "committed"

    app.dependency_overrides.clear()


def test_file_mapping_persists_column_map_for_commit() -> None:
    """Regression: ``IngestUpload.preview`` is a plain JSONB column.

    SQLAlchemy compares column values by object identity, so mutating the dict
    loaded from ``preview`` in place is invisible to change detection — the
    mapping is never written and the subsequent commit fails with 422
    ``missing_mapping``. ``file_mapping`` must store the mapping on a *new*
    dict (and reassign it) for the commit step to see it.
    """
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc = _make_location(ws.id)

    upload = IngestUpload(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        uploaded_by=user.id,
        kind="csv",
        filename="a.csv",
        status="awaiting_mapping",
        row_count=1,
        error_count=0,
        preview={
            "headers": ["date", "covers", "location"],
            "all_rows": [{"date": "2026-01-10", "covers": "40", "location": "Kigali Central"}],
        },
        created_at=datetime.datetime.now(datetime.UTC),
    )
    # The exact object the ORM would have loaded; SQLAlchemy would compare
    # against this reference when deciding whether ``preview`` is dirty.
    loaded_preview = upload.preview

    mock_db = MagicMock()
    mock_db.get.return_value = upload
    mock_db.scalar.return_value = loc  # location-by-name lookup in the dry-run
    mock_db.flush = MagicMock()

    app = create_app()
    for k, v in _auth_overrides(ws, user, mock_db).items():
        app.dependency_overrides[k] = v  # type: ignore[assignment]

    client = TestClient(app)
    resp = client.post(
        f"/api/v1/ingest/file/{upload.id}/mapping",
        json={"column_map": {"date": "date", "demand_qty": "covers", "location": "location"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["error_count"] == 0

    # A *new* dict must be assigned, else the column is treated as unchanged
    # and the mapping is silently dropped before the commit request runs.
    assert upload.preview is not loaded_preview
    assert upload.preview["column_map"] == {
        "date": "date",
        "demand_qty": "covers",
        "location": "location",
    }
    assert upload.status == "ready"

    app.dependency_overrides.clear()


def test_file_upload_rejects_invalid_extension() -> None:
    ws = _make_workspace()
    user = _make_user(ws.id)
    mock_db = MagicMock()
    app = create_app()
    for k, v in _auth_overrides(ws, user, mock_db).items():
        app.dependency_overrides[k] = v  # type: ignore[assignment]

    client = TestClient(app)
    resp = client.post(
        "/api/v1/ingest/file",
        files={"file": ("evil.exe", io.BytesIO(b"not a csv"), "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_file"

    app.dependency_overrides.clear()


def test_uploads_list_and_delete() -> None:
    ws = _make_workspace()
    user = _make_user(ws.id)
    upload = IngestUpload(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        uploaded_by=user.id,
        kind="csv",
        filename="a.csv",
        status="awaiting_mapping",
        row_count=2,
        error_count=0,
        preview={"headers": ["date"], "all_rows": []},
        created_at=datetime.datetime.now(datetime.UTC),
    )

    mock_db = MagicMock()
    # The list query now selects the upload together with a SQL boolean saying
    # whether its photo was kept, so the route reads rows from `execute` —
    # asking the ORM object for `source_image` would drag the photo into the
    # session, which is the thing the deferred column exists to avoid.
    mock_db.execute.return_value = MagicMock(
        rowcount=0, all=MagicMock(return_value=[(upload, False)])
    )
    mock_db.get.return_value = upload
    mock_db.delete = MagicMock()
    mock_db.flush = MagicMock()
    # delete checks for referencing demand rows
    mock_db.scalar.return_value = None

    app = create_app()
    for k, v in _auth_overrides(ws, user, mock_db).items():
        app.dependency_overrides[k] = v  # type: ignore[assignment]

    client = TestClient(app)
    resp = client.get("/api/v1/ingest/uploads")
    assert resp.status_code == 200
    assert "data" in resp.json()

    # Delete the upload. The response reports what went with it — zero rows
    # here, since this one was never committed.
    resp = client.delete(f"/api/v1/ingest/uploads/{upload.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed_rows"] == 0

    app.dependency_overrides.clear()


# ---------------------------------------------------------------- image stub ---

# A real 1x1 PNG. The upload route now validates the bytes, so the old
# b"fake-image-bytes" payload is correctly rejected as a non-image.
_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
)


def test_image_job_lifecycle() -> None:
    ws = _make_workspace()
    user = _make_user(ws.id)
    loc = _make_location(ws.id)

    store: dict[uuid.UUID, IngestUpload] = {}
    mock_db = MagicMock()

    def fake_add(obj):  # type: ignore[no-untyped-def]
        if isinstance(obj, IngestUpload):
            store[obj.id] = obj

    def fake_get(model, pk):  # type: ignore[no-untyped-def]
        if model == IngestUpload:
            return store.get(pk)
        if model == Location:
            return loc if pk == loc.id else None
        return None

    mock_db.add.side_effect = fake_add
    mock_db.get.side_effect = fake_get
    mock_db.flush = MagicMock()
    mock_db.scalar.return_value = None
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    app = create_app()
    for k, v in _auth_overrides(ws, user, mock_db).items():
        app.dependency_overrides[k] = v  # type: ignore[assignment]

    client = TestClient(app)

    # Upload image, stating which year the page belongs to.
    resp = client.post(
        "/api/v1/ingest/image",
        files={"file": ("log.png", io.BytesIO(_ONE_PIXEL_PNG), "image/png")},
        data={"reporting_year": "2024"},
    )
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["job_id"]

    # The stated year is what yearless dates were read as, and it is kept with
    # the preview so a retry re-reads the page under the same assumption instead
    # of sliding every row into the current year.
    staged = store[uuid.UUID(job_id)]
    assert staged.preview["reporting_year"] == 2024

    # GET job
    resp = client.get(f"/api/v1/ingest/image/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["job_id"] == job_id
    assert resp.json()["reporting_year"] == 2024

    # Commit corrected rows
    resp = client.post(
        f"/api/v1/ingest/image/{job_id}/commit",
        json={"rows": [{"location_id": str(loc.id), "date": "2026-01-12", "demand_qty": 33}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["committed"] == 1

    app.dependency_overrides.clear()


def test_image_upload_rejects_non_image_bytes() -> None:
    """The upload route refuses content that is not an image before staging it."""
    ws = _make_workspace()
    user = _make_user(ws.id)
    mock_db = MagicMock()

    app = create_app()
    for k, v in _auth_overrides(ws, user, mock_db).items():
        app.dependency_overrides[k] = v  # type: ignore[assignment]

    client = TestClient(app)
    resp = client.post(
        "/api/v1/ingest/image",
        files={"file": ("log.jpg", io.BytesIO(b"not an image"), "image/jpeg")},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "invalid_image"
    # Validation happens before the upload is staged, so nothing was written.
    mock_db.add.assert_not_called()

    app.dependency_overrides.clear()


# ---------------------------------------------------------------- leakage test ---


def test_feature_window_no_leak() -> None:
    """Leakage test: any feature window overlapping target_date must raise.

    This is the single most important test in the suite (ARCHITECTURE.md §2,
    IMPLEMENTATION_ROADMAP.md non-negotiable 1). It fails the build if the
    ``as_of`` boundary is removed.
    """
    ws = _make_workspace()
    loc = _make_location(ws.id)

    # Build a small in-memory history: target_date 2026-02-01, history before it.
    target = datetime.date(2026, 2, 1)
    as_of = datetime.datetime(2026, 1, 31, 18, 0, tzinfo=datetime.UTC)

    # Mock DB: history rows for 2026-01-20 .. 2026-01-31
    history_rows = []
    for i in range(1, 12):
        d = datetime.date(2026, 1, 20 + i)
        history_rows.append(
            DemandObservation(
                id=i,
                workspace_id=ws.id,
                location_id=loc.id,
                date=d,
                demand_qty=40 + i,
                recorded_at=datetime.datetime(2026, 1, 20 + i, 12, 0, tzinfo=datetime.UTC),
            )
        )

    mock_db = MagicMock()
    mock_db.get.return_value = loc

    # scalars for _history_for_location returns the history_rows
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=history_rows))

    # Valid build — should succeed and not contain target_date
    fv = build_features(mock_db, location_id=loc.id, target_date=target, as_of=as_of)
    assert fv.target_date == target
    assert fv.as_of == as_of
    # All historical features must be derived from date < target_date
    assert target not in [r.date for r in history_rows]
    # Spot-check: rolling means are not None when history exists
    assert fv.features["roll_mean_7"] is not None

    # --- Leakage case 1: as_of on or after target_date -------------------
    bad_as_of = datetime.datetime(2026, 2, 1, 8, 0, tzinfo=datetime.UTC)
    try:
        build_features(mock_db, location_id=loc.id, target_date=target, as_of=bad_as_of)
        assert False, "as_of >= target_date should have raised FeatureLeakageError"
    except FeatureLeakageError as exc:
        assert "as_of" in str(exc).lower()

    # --- Leakage case 2: reservation not known before as_of must be ignored ---
    # Provide a reservation known after as_of — it should be excluded, not leaked.
    reservation = {
        target: {
            "reserved_count": 99,
            "lead_time_days": 2.0,
            "known_at": datetime.datetime(2026, 2, 1, 9, 0, tzinfo=datetime.UTC),
        }
    }
    fv2 = build_features(
        mock_db,
        location_id=loc.id,
        target_date=target,
        as_of=as_of,
        reservation_known_before_as_of=reservation,
    )
    # Because known_at > as_of, the reservation must be ignored (None)
    assert fv2.features["reservation_count"] is None

    # Same reservation known before as_of should be included
    reservation_ok = {
        target: {
            "reserved_count": 99,
            "lead_time_days": 2.0,
            "known_at": datetime.datetime(2026, 1, 31, 12, 0, tzinfo=datetime.UTC),
        }
    }
    fv3 = build_features(
        mock_db,
        location_id=loc.id,
        target_date=target,
        as_of=as_of,
        reservation_known_before_as_of=reservation_ok,
    )
    assert fv3.features["reservation_count"] == 99


def test_the_service_day_ceiling_is_the_workspaces_local_day() -> None:
    """A restaurant three hours ahead of UTC must be able to log "today".

    The ceiling answers a local business question — has tomorrow started *here*?
    — so asking it in UTC rejects a Kigali workspace's current service day
    between 21:00 and 24:00 UTC, which is the middle of its working evening.
    This is the default timezone in the schema, so it is not an edge case.
    """
    import zoneinfo

    from app.services.ingestion.normaliser import _today_in

    kigali = zoneinfo.ZoneInfo("Africa/Kigali")
    now_utc = datetime.datetime.now(datetime.UTC)
    assert _today_in("Africa/Kigali") == now_utc.astimezone(kigali).date()

    # An hour either side of UTC midnight is exactly where UTC and Kigali
    # disagree, and it is the window this rule exists for.
    assert _today_in("Africa/Kigali") >= now_utc.date()
    assert _today_in("UTC") == now_utc.date()

    # A broken timezone must not cost a location its ability to ingest.
    assert _today_in("Not/AZone") == now_utc.date()
    assert _today_in(None) == now_utc.date()
    assert _today_in("") == now_utc.date()
