"""Real-Postgres integration tests for the ingestion lifecycle.

Deliberately **not** part of the hermetic unit suite: these need a migrated
PostgreSQL database and are skipped unless ``DEMANDIQ_RUN_INTEGRATION`` is set.
Run them with ``make test-backend-integration``
(``backend/scripts/ingest_e2e.sh``), which boots a disposable cluster, runs
``alembic upgrade head`` and exports both variables. ``make test-backend``
therefore stays hermetic no matter what ``DATABASE_URL`` happens to be in the
developer's shell.

Why they exist: ``tests/test_ingest.py`` mocks the session, and a mock cannot
show round-trip behaviour. Concretely, ``ingest_uploads.preview`` is a JSONB
column; mutating the dict loaded from it in place was invisible to SQLAlchemy,
so the column-map written by ``/mapping`` never reached the database and
``/commit`` failed with ``422 missing_mapping`` — while the mocked unit test
kept passing. Every request here gets its *own* session, exactly like
production, so that class of bug cannot hide again.
"""

from __future__ import annotations

import datetime
import io
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.database import get_db, get_engine
from app.deps import get_current_user
from app.main import create_app
from app.models.location import DemandObservation, IngestUpload, Location
from app.models.user import User, UserRole
from app.models.workspace import Workspace
from app.services.ingestion.normaliser import commit_demand

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("DEMANDIQ_RUN_INTEGRATION"),
        reason="needs a migrated Postgres — run scripts/ingest_e2e.sh",
    ),
]


def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def _seed(prefix: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Insert a workspace + owner + location; return their ids."""
    with _session_factory()() as db:
        ws = Workspace(id=uuid.uuid4(), name=f"{prefix} Co", country="RW", timezone="Africa/Kigali")
        user = User(
            id=uuid.uuid4(),
            workspace_id=ws.id,
            name=f"{prefix} User",
            email=f"{prefix.lower()}-{uuid.uuid4().hex[:10]}@example.com",
            password_hash="x",
            role=UserRole.owner,
            email_verified=True,
        )
        loc = Location(id=uuid.uuid4(), workspace_id=ws.id, name="Kigali Central")
        db.add_all([ws, user, loc])
        db.commit()
        return ws.id, user.id, loc.id


def _client(user_id: uuid.UUID) -> TestClient:
    """App in which every request opens its own session, like production.

    A request-scoped session matters: a shared one would leak the mapping
    step's writes to the commit step through the identity map even if nothing
    was ever persisted.
    """
    app = create_app()

    def override_db():
        db = _session_factory()()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def override_user() -> User | None:
        with _session_factory()() as db:
            return db.get(User, user_id)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    return TestClient(app)


# --------------------------------------------------------------------------- #
# The lifecycle the Excel/CSV screen drives
# --------------------------------------------------------------------------- #


def test_file_upload_mapping_commit_persists_rows() -> None:
    """upload -> mapping -> commit works across separate, real DB sessions."""
    ws_id, user_id, _ = _seed("Integration")
    client = _client(user_id)

    csv_text = "date,covers,location\n2026-01-10,40,Kigali Central\n2026-01-11,55,Kigali Central\n"
    resp = client.post(
        "/api/v1/ingest/file",
        files={"file": ("demand.csv", io.BytesIO(csv_text.encode()), "text/csv")},
    )
    assert resp.status_code == 201, resp.text
    upload_id = resp.json()["upload_id"]

    resp = client.post(
        f"/api/v1/ingest/file/{upload_id}/mapping",
        json={"column_map": {"date": "date", "demand_qty": "covers", "location": "location"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["error_count"] == 0
    assert resp.json()["mapped_rows"] == 2

    # The mapping must be visible to a *new* session — this is precisely what
    # the JSONB identity-tracking bug broke, and the source of the 422.
    with _session_factory()() as db:
        staged = db.get(IngestUpload, uuid.UUID(upload_id))
        assert staged is not None
        assert staged.status == "ready"
        assert staged.preview["column_map"]["demand_qty"] == "covers"

    resp = client.post(f"/api/v1/ingest/file/{upload_id}/commit")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] + body["reconciled"] == 2
    assert body["status"] == "committed"

    with _session_factory()() as db:
        rows = db.scalars(
            select(DemandObservation).where(DemandObservation.workspace_id == ws_id)
        ).all()
        assert {r.date for r in rows} == {datetime.date(2026, 1, 10), datetime.date(2026, 1, 11)}
        assert {r.demand_qty for r in rows} == {40, 55}

    # Re-committing is refused rather than double-counting.
    resp = client.post(f"/api/v1/ingest/file/{upload_id}/commit")
    assert resp.status_code == 409, resp.text
    with _session_factory()() as db:
        rows = db.scalars(
            select(DemandObservation).where(DemandObservation.workspace_id == ws_id)
        ).all()
        assert len(rows) == 2


def test_demand_rows_can_be_traced_back_to_the_import_that_wrote_them() -> None:
    """`GET /ingest/demand?upload_id=` returns exactly the rows one import wrote.

    The spreadsheet screen's trace is only as good as this filter. A manager
    asking "where did these numbers come from?" needs the answer to be an
    equality on the row's own ``upload_id``, not a guess at it by matching dates
    or the filename in free-text notes — either of which would also match a
    manual entry that merely happened to mention the file.
    """
    ws_id, user_id, _ = _seed("Trace")
    client = _client(user_id)

    def import_file(name: str, text: str) -> str:
        resp = client.post(
            "/api/v1/ingest/file",
            files={"file": (name, io.BytesIO(text.encode()), "text/csv")},
        )
        assert resp.status_code == 201, resp.text
        uid = resp.json()["upload_id"]
        resp = client.post(
            f"/api/v1/ingest/file/{uid}/mapping",
            json={"column_map": {"date": "date", "demand_qty": "covers", "location": "location"}},
        )
        assert resp.status_code == 200, resp.text
        resp = client.post(f"/api/v1/ingest/file/{uid}/commit")
        assert resp.status_code == 200, resp.text
        return uid

    january = import_file(
        "january.csv",
        "date,covers,location\n2026-01-10,40,Kigali Central\n2026-01-11,55,Kigali Central\n",
    )
    february = import_file("february.csv", "date,covers,location\n2026-02-10,61,Kigali Central\n")

    resp = client.get("/api/v1/ingest/demand", params={"upload_id": january})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    # Exactly the two dates that file carried, not the other import's row.
    assert {r["date"] for r in body["data"]} == {"2026-01-10", "2026-01-11"}
    # ...and the row names its own origin, so the screen can say which file.
    assert {r["upload_id"] for r in body["data"]} == {january}
    assert {r["upload_filename"] for r in body["data"]} == {"january.csv"}

    resp = client.get("/api/v1/ingest/demand", params={"upload_id": february})
    assert {r["date"] for r in resp.json()["data"]} == {"2026-02-10"}

    # The unfiltered list still carries provenance for file rows, and reports no
    # file for a row that never came from one.
    resp = client.get("/api/v1/ingest/demand")
    assert resp.status_code == 200, resp.text
    assert {r["upload_filename"] for r in resp.json()["data"]} == {
        "january.csv",
        "february.csv",
    }

    with _session_factory()() as db:
        commit_demand(
            db,
            workspace_id=ws_id,
            location_id=db.scalars(select(Location).where(Location.workspace_id == ws_id)).one().id,
            date=datetime.date(2026, 3, 1),
            demand_qty=77,
            source="manual",
        )
        db.commit()

    resp = client.get("/api/v1/ingest/demand", params={"limit": 50})
    manual = next(r for r in resp.json()["data"] if r["date"] == "2026-03-01")
    assert manual["upload_id"] is None
    assert manual["upload_filename"] is None

    # An upload id from nowhere matches nothing rather than erroring or leaking.
    resp = client.get("/api/v1/ingest/demand", params={"upload_id": str(uuid.uuid4())})
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 0


def test_jsonb_in_place_mutation_is_persisted() -> None:
    """Guards the whole bug class: in-place JSONB edits must reach the database.

    ``MutableJSONB`` marks the attribute dirty on ``dict[key] = value``. Before
    it, only replacing the whole dict was written, so partial updates (the
    column map, ACI state, alert thresholds, ...) vanished silently.
    """
    ws_id, user_id, _ = _seed("Jsonb")
    upload_id = uuid.uuid4()

    with _session_factory()() as db:
        db.add(
            IngestUpload(
                id=upload_id,
                workspace_id=ws_id,
                uploaded_by=user_id,
                kind="csv",
                filename="inplace.csv",
                status="awaiting_mapping",
                row_count=0,
                error_count=0,
                preview={"headers": ["date"], "all_rows": []},
            )
        )
        db.commit()

    # Mutate in place — the exact pattern that used to be dropped.
    with _session_factory()() as db:
        row = db.get(IngestUpload, upload_id)
        assert row is not None
        row.preview["column_map"] = {"date": "date"}
        row.preview["dry_run"] = {"mapped_count": 0}
        db.commit()

    with _session_factory()() as db:
        row = db.get(IngestUpload, upload_id)
        assert row is not None
        assert row.preview["column_map"] == {"date": "date"}
        assert row.preview["dry_run"] == {"mapped_count": 0}
        # Keys that were not touched must survive the rewrite.
        assert row.preview["headers"] == ["date"]
