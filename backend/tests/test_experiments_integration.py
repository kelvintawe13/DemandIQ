"""Real-Postgres integration tests for the Experiments screen.

Deliberately **not** part of the hermetic unit suite: these need a migrated
PostgreSQL database and are skipped unless ``DEMANDIQ_RUN_INTEGRATION`` is set.
Run them with ``make test-backend-integration``.

Why they exist: ``GET /api/v1/experiments`` wraps its query in a ``try/except``
so a mocked session cannot 500 the screen — but that also meant a real schema
drift was invisible. The ``Experiment`` model declared a ``created_at`` column
the database did not have, so every select raised ``ProgrammingError`` and the
endpoint answered with an empty list: the screen showed its empty state no
matter how many rows existed, and ``POST`` would have failed on insert. A unit
test with a mocked session cannot catch that; a test against the migrated schema
can. These pin the round trip so the class of bug cannot return silently.
"""

from __future__ import annotations

import datetime
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.database import get_db, get_engine
from app.deps import get_current_user
from app.main import create_app
from app.models.experiment import Experiment, ExperimentFold
from app.models.user import User, UserRole
from app.models.workspace import Workspace

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
    """Insert a workspace, an administrator, and one backtest; return their ids."""
    stamp = uuid.uuid4().hex[:10]
    now = datetime.datetime.now(datetime.UTC)
    with _session_factory()() as db:
        ws = Workspace(
            id=uuid.uuid4(), name=f"{prefix} Co {stamp}", country="RW", timezone="Africa/Kigali"
        )
        user = User(
            id=uuid.uuid4(),
            workspace_id=ws.id,
            name=f"{prefix} Admin",
            email=f"{prefix.lower()}-admin-{stamp}@example.com",
            password_hash="x",
            role=UserRole.administrator,
            email_verified=True,
        )
        # Commit the parents first: the experiment references both, and a single
        # flush cannot rely on insert ordering across two foreign keys.
        db.add_all([ws, user])
        db.commit()
        exp = Experiment(
            id=uuid.uuid4(),
            workspace_id=ws.id,
            name="Seeded rolling-origin backtest",
            hypothesis="The champion generalises across expanding windows.",
            config={"algorithm": "lightgbm", "feature_set": "fs-test", "folds": 2},
            status="completed",
            mae=6.667,
            baseline_mae=7.67,
            created_by=user.id,
            finished_at=now,
            created_at=now,
        )
        db.add(exp)
        db.flush()
        db.add_all(
            [
                ExperimentFold(
                    id=uuid.uuid4(),
                    experiment_id=exp.id,
                    fold_index=0,
                    train_start=datetime.date(2016, 1, 2),
                    train_end=datetime.date(2017, 1, 13),
                    test_start=datetime.date(2017, 1, 13),
                    test_end=datetime.date(2017, 2, 12),
                    mae=6.884,
                    coverage=0.91,
                ),
                ExperimentFold(
                    id=uuid.uuid4(),
                    experiment_id=exp.id,
                    fold_index=1,
                    train_start=datetime.date(2016, 1, 2),
                    train_end=datetime.date(2017, 1, 27),
                    test_start=datetime.date(2017, 1, 27),
                    test_end=datetime.date(2017, 2, 26),
                    mae=7.101,
                    coverage=0.90,
                ),
            ]
        )
        db.commit()
        return ws.id, user.id, exp.id


def _client(user_id: uuid.UUID) -> TestClient:
    """App in which every request opens its own session, like production."""
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


def test_list_returns_seeded_experiment_from_the_real_schema() -> None:
    """The regression: a row that exists must be listed, not swallowed to []."""
    ws_id, user_id, exp_id = _seed("Experiments")
    client = _client(user_id)

    resp = client.get("/api/v1/experiments")
    assert resp.status_code == 200, resp.text
    rows = resp.json()["data"]
    ids = {row["id"] for row in rows}
    assert str(exp_id) in ids, "seeded experiment missing — the list query failed silently"
    row = next(r for r in rows if r["id"] == str(exp_id))
    assert row["name"] == "Seeded rolling-origin backtest"
    assert row["status"] == "completed"
    assert float(row["mae"]) == 6.667


def test_detail_exposes_folds_and_created_at_round_trips() -> None:
    ws_id, user_id, exp_id = _seed("Experiments")
    client = _client(user_id)

    resp = client.get(f"/api/v1/experiments/{exp_id}")
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert detail["id"] == str(exp_id)
    assert len(detail["folds"]) == 2
    assert [f["fold_index"] for f in detail["folds"]] == [0, 1]
    assert float(detail["folds"][0]["mae"]) == 6.884

    # `created_at` is the column that was missing; it must persist and read back.
    with _session_factory()() as db:
        stored = db.scalar(select(Experiment).where(Experiment.id == exp_id))
        assert stored is not None
        assert stored.created_at is not None


def test_create_is_refused_and_leaves_no_row() -> None:
    """A request cannot fabricate a backtest it never ran.

    The endpoint used to insert a ``completed`` experiment whose MAE and
    coverage came from ``uuid4()``, so the screen showed a measured result that
    no run produced. The refusal must also write nothing: a rejected request
    that still leaves a row would be the same lie in a different place.
    """
    ws_id, user_id, _ = _seed("Experiments")
    client = _client(user_id)

    resp = client.post(
        "/api/v1/experiments",
        json={
            "name": "Created by API",
            "hypothesis": "A queued backtest still records its folds.",
            "algorithm": "catboost",
            "feature_set": "fs-test",
            "folds": 2,
        },
    )
    assert resp.status_code == 501, resp.text
    assert resp.json()["error"]["code"] == "backtests_not_run_here"

    with _session_factory()() as db:
        names = {
            row.name
            for row in db.scalars(
                select(Experiment).where(Experiment.workspace_id == ws_id)
            )
        }
    assert "Created by API" not in names
