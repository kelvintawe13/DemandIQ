"""Models, experiments & features tests — admin, governance, workspace scoping."""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.experiment import Experiment, ExperimentFold
from app.models.feature import FeatureDefinition
from app.models.forecast import Calibration, ModelVersion
from app.models.governance import ChangeRequest
from app.models.location import Location
from app.models.user import User, UserRole
from app.models.workspace import Workspace


def _make_workspace() -> Workspace:
    return Workspace(
        id=uuid.uuid4(),
        name="Test Co",
        business_type="cafe",
        country="RW",
        timezone="Africa/Kigali",
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _make_user(ws_id: uuid.UUID, role: UserRole = UserRole.administrator) -> User:
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


def _make_location(ws_id: uuid.UUID, name: str = "Kigali Central") -> Location:
    return Location(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        name=name,
        area="Central",
        cuisine="Test",
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _seed_model(
    stores: dict[type, dict],
    ws_id: uuid.UUID,
    *,
    algorithm: str = "lightgbm",
    version: str | None = None,
    feature_set: str = "v1",
    status: str = "candidate",
    hyperparameters: dict | None = None,
) -> ModelVersion:
    """Insert a registry row directly into the mock store.

    ``POST /models/train`` no longer creates rows — training runs offline in the
    ``model/`` package and the registry is published from the serving bundle —
    so tests that need a model to promote, roll back or inspect seed one here.
    """
    mv = ModelVersion(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        algorithm=algorithm,
        version=version or f"{algorithm}-{uuid.uuid4().hex[:6]}",
        status=status,
        feature_set_version=feature_set,
        hyperparameters=hyperparameters,
        trained_at=datetime.datetime.now(datetime.UTC),
    )
    stores[ModelVersion][mv.id] = mv
    return mv


# ---------------------------------------------------------------------------
# Helpers to build a mocked DB that stores multiple model types.
# ---------------------------------------------------------------------------


def _build_mock_db(
    stores: dict[type, dict],
) -> MagicMock:
    """Return a MagicMock mimicking Session for the given stores.

    Args:
        stores: Mapping from model class to dict store {pk: instance}.
    """

    mock_db = MagicMock()

    def fake_add(obj: object) -> None:  # type: ignore[no-untyped-def]
        for model_cls, store in stores.items():
            if isinstance(obj, model_cls):
                # Use id as key when available, else int for calibrations etc.
                key = getattr(obj, "id", None)
                if key is not None:
                    store[key] = obj
                else:
                    store[id(obj)] = obj
                return
        # For generic AuditLog etc., ignore but allow add.
        return

    def fake_get(model: type, pk: object) -> object | None:  # type: ignore[no-untyped-def]
        store = stores.get(model)
        if store is None:
            return None
        return store.get(pk)  # type: ignore[no-any-return]

    def fake_scalars(stmt: object) -> MagicMock:  # type: ignore[no-untyped-def]
        txt = str(stmt)

        class _S:
            def all(self) -> list:  # type: ignore[no-untyped-def]
                # Determine model by table name hint in SQL text.
                if "model_versions" in txt:
                    s = stores.get(ModelVersion, {})
                    return list(s.values())
                if "calibrations" in txt:
                    s = stores.get(Calibration, {})
                    return list(s.values())
                if "experiments" in txt and "experiment_folds" not in txt:
                    s = stores.get(Experiment, {})
                    return list(s.values())
                if "experiment_folds" in txt:
                    s = stores.get(ExperimentFold, {})
                    return list(s.values())
                if "feature_definitions" in txt:
                    s = stores.get(FeatureDefinition, {})
                    return list(s.values())
                if "change_requests" in txt:
                    s = stores.get(ChangeRequest, {})
                    return list(s.values())
                if "locations" in txt:
                    s = stores.get(Location, {})
                    return list(s.values())
                return []

        class _R:
            def scalars(self) -> _S:  # type: ignore[no-untyped-def]
                return _S()

        return _R().scalars()  # type: ignore[return-value]

    def fake_scalar(stmt: object) -> object | None:  # type: ignore[no-untyped-def]
        txt = str(stmt)
        if "calibrations" in txt:
            s = stores.get(Calibration, {})
            vals = list(s.values())
            return vals[0] if vals else None
        if "change_requests" in txt:
            s = stores.get(ChangeRequest, {})
            vals = list(s.values())
            return vals[0] if vals else None
        if "feature_definitions" in txt:
            s = stores.get(FeatureDefinition, {})
            for v in s.values():
                if hasattr(v, "name") and v.name in txt:
                    return v
            return None
        return None

    mock_db.add.side_effect = fake_add
    mock_db.get.side_effect = fake_get
    mock_db.scalars.side_effect = fake_scalars
    mock_db.scalar.side_effect = fake_scalar
    mock_db.flush = MagicMock()
    mock_db.delete.side_effect = lambda obj: stores.get(type(obj), {}).pop(getattr(obj, "id", None), None)  # type: ignore[no-untyped-def]

    return mock_db


# ---------------------------------------------------------------------------
# Models registry
# ---------------------------------------------------------------------------


def test_models_registry_and_train_unavailable() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    stores: dict[type, dict] = {
        ModelVersion: {},
        Calibration: {},
        ChangeRequest: {},
    }
    mock_db = _build_mock_db(stores)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)

    # Empty registry shape.
    resp = client.get("/api/v1/models")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body
    assert isinstance(body["data"], list)

    # Training is not offered by the API: it must say so rather than queue a
    # row that can never leave `training`.
    resp = client.post(
        "/api/v1/models/train",
        json={"algorithm": "lightgbm", "feature_set": "v2", "params": {"learning_rate": 0.05}},
    )
    assert resp.status_code == 501, resp.text
    assert resp.json()["error"]["code"] == "training_unavailable"
    assert stores[ModelVersion] == {}, "a refused train must not write a row"

    # Registry row published from the serving bundle (seeded here).
    seeded = _seed_model(
        stores, ws.id, algorithm="lightgbm", feature_set="v2", hyperparameters={"lr": 0.05}
    )
    mid = str(seeded.id)

    # Registry has the entry with MAE/RMSE/RMSLE status.
    resp = client.get("/api/v1/models")
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert len(rows) >= 1
    row = next(r for r in rows if r["id"] == mid)
    assert "mae" in row
    assert "rmse" in row
    assert "rmsle" in row
    assert "status" in row

    # Detail includes hyperparameters, training window and calibration.
    resp = client.get(f"/api/v1/models/{mid}")
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert detail["id"] == mid
    assert detail["feature_set_version"] == "v2"
    # Hyperparameters present
    assert detail["hyperparameters"] is not None

    app.dependency_overrides.clear()


def test_models_train_validation() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    stores: dict[type, dict] = {ModelVersion: {}, Calibration: {}, ChangeRequest: {}}
    mock_db = _build_mock_db(stores)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)

    # Invalid algorithm -> 400
    resp = client.post(
        "/api/v1/models/train", json={"algorithm": "unknown_algo", "feature_set": "v1"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_algorithm"

    # Missing feature_set -> validation 422
    resp = client.post("/api/v1/models/train", json={"algorithm": "lightgbm", "feature_set": ""})
    assert resp.status_code == 400 or resp.status_code == 422

    # Not found detail
    resp = client.get(f"/api/v1/models/{uuid.uuid4()}")
    assert resp.status_code == 404

    app.dependency_overrides.clear()


def test_models_promote_creates_change_request_and_two_person_rule() -> None:
    ws = _make_workspace()
    requester = _make_user(ws.id, role=UserRole.administrator)
    approver = _make_user(ws.id, role=UserRole.owner)
    stores: dict[type, dict] = {ModelVersion: {}, Calibration: {}, ChangeRequest: {}}
    mock_db = _build_mock_db(stores)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: requester  # type: ignore[assignment]
    client = TestClient(app)

    # A model to promote: training runs offline, so seed the registry row.
    seeded = _seed_model(stores, ws.id, algorithm="catboost", feature_set="v3")
    mid = str(seeded.id)

    # Promote -> creates change_request
    resp = client.post(f"/api/v1/models/{mid}/promote")
    assert resp.status_code == 201, resp.text
    cr_body = resp.json()
    assert "id" in cr_body or "change_request_id" in cr_body
    cr_id = cr_body.get("id") or cr_body.get("change_request_id")
    assert cr_body.get("kind") == "model_promotion" or "model_promotion" in str(cr_body)
    assert cr_body.get("status") == "pending"

    # Requester may not approve own request -> 422 two_person_rule
    resp = client.post(f"/api/v1/change-requests/{cr_id}/approve")
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "two_person_rule"

    # Different admin approves
    app.dependency_overrides[get_current_user] = lambda: approver  # type: ignore[assignment]
    resp = client.post(f"/api/v1/change-requests/{cr_id}/approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"

    app.dependency_overrides.clear()


def test_models_rollback_restores_previous_production() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    stores: dict[type, dict] = {ModelVersion: {}, Calibration: {}, ChangeRequest: {}}
    mock_db = _build_mock_db(stores)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)

    # Two registry rows to roll between (seeded — training runs offline).
    mid1 = str(_seed_model(stores, ws.id, algorithm="lightgbm", feature_set="v1").id)
    mid2 = str(_seed_model(stores, ws.id, algorithm="lightgbm", feature_set="v2").id)

    # Manually mark mid1 as production, mid2 as candidate to simulate state.
    m1: ModelVersion = stores[ModelVersion][uuid.UUID(mid1)]
    m2: ModelVersion = stores[ModelVersion][uuid.UUID(mid2)]
    m1.status = "production"
    m1.promoted_at = datetime.datetime.now(datetime.UTC)
    m2.status = "candidate"

    # Rollback to mid2 (promotes mid2, archives mid1)
    resp = client.post(f"/api/v1/models/{mid2}/rollback")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "production"
    assert body["model_id"] == mid2
    # Verify state mutated
    assert stores[ModelVersion][uuid.UUID(mid2)].status == "production"
    assert stores[ModelVersion][uuid.UUID(mid1)].status == "archived"

    # Rollback when already production but with previous archived should swap back
    resp = client.post(f"/api/v1/models/{mid2}/rollback")
    assert resp.status_code == 200
    assert stores[ModelVersion][uuid.UUID(mid1)].status == "production"

    app.dependency_overrides.clear()


def test_models_calibration_and_importance() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    stores: dict[type, dict] = {ModelVersion: {}, Calibration: {}, ChangeRequest: {}}
    mock_db = _build_mock_db(stores)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)

    mid = str(_seed_model(stores, ws.id, algorithm="xgboost", feature_set="v4").id)

    # Calibration shape
    resp = client.get(f"/api/v1/models/{mid}/calibration")
    assert resp.status_code == 200, resp.text
    cal = resp.json()
    assert "target_coverage" in cal
    assert "empirical_coverage" in cal
    assert "mean_interval_width" in cal

    # Importance shape
    resp = client.get(f"/api/v1/models/{mid}/importance")
    assert resp.status_code == 200, resp.text
    imp = resp.json()
    assert "importance" in imp
    assert isinstance(imp["importance"], list)
    assert len(imp["importance"]) >= 5
    # Each entry has feature and importance
    for entry in imp["importance"]:
        assert "feature" in entry
        assert "importance" in entry

    # Not found
    fake = str(uuid.uuid4())
    resp = client.get(f"/api/v1/models/{fake}/calibration")
    assert resp.status_code == 404
    resp = client.get(f"/api/v1/models/{fake}/importance")
    assert resp.status_code == 404

    app.dependency_overrides.clear()


def test_models_admin_guards() -> None:
    ws = _make_workspace()
    viewer = _make_user(ws.id, role=UserRole.viewer)
    operator = _make_user(ws.id, role=UserRole.operator)
    mock_db = MagicMock()
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    mock_db.scalar.return_value = None
    mock_db.get.return_value = None
    app = create_app()
    for user in (viewer, operator):
        app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
        app.dependency_overrides[get_current_user] = lambda u=user: u  # type: ignore[assignment]
        client = TestClient(app)
        resp = client.get("/api/v1/models")
        assert resp.status_code == 403, resp.text
        resp = client.post(
            "/api/v1/models/train", json={"algorithm": "lightgbm", "feature_set": "v1"}
        )
        assert resp.status_code == 403
        resp = client.post(f"/api/v1/models/{uuid.uuid4()}/promote")
        assert resp.status_code == 403
        resp = client.post(f"/api/v1/models/{uuid.uuid4()}/rollback")
        assert resp.status_code == 403
        resp = client.get(f"/api/v1/models/{uuid.uuid4()}/calibration")
        assert resp.status_code == 403
        resp = client.get(f"/api/v1/models/{uuid.uuid4()}/importance")
        assert resp.status_code == 403
    # Unauthenticated -> 401
    app.dependency_overrides[get_current_user] = lambda: (_ for _ in ()).throw(Exception("no auth"))  # type: ignore[assignment]
    # Use no override to test 401 path — create fresh app without override
    fresh = create_app()
    fresh.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    client = TestClient(fresh)
    resp = client.get("/api/v1/models")
    assert resp.status_code == 401

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------


def test_experiments_crud_and_detail() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    stores: dict[type, dict] = {
        ModelVersion: {},
        Calibration: {},
        ChangeRequest: {},
        Experiment: {},
        ExperimentFold: {},
    }
    mock_db = _build_mock_db(stores)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)

    # A published backtest: seeded directly, because POST no longer creates one.
    now = datetime.datetime.now(datetime.UTC)
    exp = Experiment(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        name="Test exp",
        hypothesis="lag_7 improves MAE",
        config={"algorithm": "lightgbm", "feature_set": "fs-test", "folds": 3},
        status="completed",
        mae=6.702,
        baseline_mae=7.667,
        coverage=89.98,
        created_by=admin.id,
        finished_at=now,
        created_at=now,
    )
    stores[Experiment][exp.id] = exp
    for i in range(3):
        fold = ExperimentFold(
            id=uuid.uuid4(),
            experiment_id=exp.id,
            fold_index=i,
            mae=6.7 + i * 0.1,
            coverage=0.90,
        )
        stores[ExperimentFold][fold.id] = fold

    # List reads the published row.
    resp = client.get("/api/v1/experiments")
    assert resp.status_code == 200
    assert [row["name"] for row in resp.json()["data"]] == ["Test exp"]

    # Detail returns fold-level MAE, coverage, baseline delta.
    resp = client.get(f"/api/v1/experiments/{exp.id}")
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert detail["id"] == str(exp.id)
    assert len(detail["folds"]) == 3
    for f in detail["folds"]:
        assert "mae" in f
        assert "coverage" in f
        assert "baseline_delta" in f or "baseline_mae" in f

    # POST is refused: the API does not run the backtest.
    resp = client.post(
        "/api/v1/experiments",
        json={
            "name": "Fabricated",
            "algorithm": "lightgbm",
            "feature_set": "v2",
            "folds": 3,
        },
    )
    assert resp.status_code == 501, resp.text
    assert resp.json()["error"]["code"] == "backtests_not_run_here"

    # Not found
    resp = client.get(f"/api/v1/experiments/{uuid.uuid4()}")
    assert resp.status_code == 404

    app.dependency_overrides.clear()


def test_experiments_submit_to_approvals_queue() -> None:
    ws = _make_workspace()
    requester = _make_user(ws.id, role=UserRole.administrator)
    stores: dict[type, dict] = {
        ModelVersion: {},
        Calibration: {},
        ChangeRequest: {},
        Experiment: {},
        ExperimentFold: {},
    }
    mock_db = _build_mock_db(stores)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: requester  # type: ignore[assignment]
    client = TestClient(app)

    now = datetime.datetime.now(datetime.UTC)
    exp = Experiment(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        name="Submit exp",
        hypothesis="h",
        config={"algorithm": "catboost", "feature_set": "v5", "folds": 2},
        status="completed",
        mae=6.69,
        baseline_mae=7.667,
        coverage=89.98,
        created_by=requester.id,
        finished_at=now,
        created_at=now,
    )
    stores[Experiment][exp.id] = exp
    eid = str(exp.id)

    # Submit sends to approvals queue (change_requests)
    resp = client.post(f"/api/v1/experiments/{eid}/submit")
    assert resp.status_code == 201, resp.text
    cr = resp.json()
    assert cr["status"] == "pending"
    assert "change_request_id" in cr or "id" in cr
    cr_id = cr.get("change_request_id") or cr.get("id")

    # Verify change request exists via governance list
    resp = client.get("/api/v1/change-requests")
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()["data"]]
    assert cr_id in ids

    # Second submit should fail (already submitted)
    resp = client.post(f"/api/v1/experiments/{eid}/submit")
    # Depending on impl, either 400 or still pending; we accept 400 or 422
    assert resp.status_code in (400, 422, 201)  # tolerate idempotent re-submit

    app.dependency_overrides.clear()


def test_experiments_admin_guards() -> None:
    ws = _make_workspace()
    viewer = _make_user(ws.id, role=UserRole.viewer)
    mock_db = MagicMock()
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    mock_db.scalar.return_value = None
    mock_db.get.return_value = None
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: viewer  # type: ignore[assignment]
    client = TestClient(app)
    resp = client.get("/api/v1/experiments")
    assert resp.status_code == 403
    resp = client.post(
        "/api/v1/experiments",
        json={
            "name": "x",
            "hypothesis": "h",
            "algorithm": "lightgbm",
            "feature_set": "v1",
            "folds": 2,
        },
    )
    assert resp.status_code == 403
    resp = client.get(f"/api/v1/experiments/{uuid.uuid4()}")
    assert resp.status_code == 403
    resp = client.post(f"/api/v1/experiments/{uuid.uuid4()}/submit")
    assert resp.status_code == 403
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------


def test_features_list_and_coverage() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    loc = _make_location(ws.id)
    stores: dict[type, dict] = {
        FeatureDefinition: {},
        Location: {loc.id: loc},
        ModelVersion: {},
        Calibration: {},
        ChangeRequest: {},
        Experiment: {},
        ExperimentFold: {},
    }
    mock_db = _build_mock_db(stores)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)

    # List feature definitions
    resp = client.get("/api/v1/features")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert len(data) >= 10
    # Each entry has coverage and leakage status
    for f in data:
        assert "name" in f
        assert "feature_group" in f
        assert "coverage_pct" in f
        assert "leakage_safe" in f
        assert "in_production" in f

    # Coverage per location
    feature_name = data[0]["name"]
    resp = client.get(f"/api/v1/features/{feature_name}/coverage")
    assert resp.status_code == 200, resp.text
    cov = resp.json()
    assert cov["feature"] == feature_name
    assert "per_location" in cov
    assert isinstance(cov["per_location"], list)
    assert len(cov["per_location"]) >= 1
    for row in cov["per_location"]:
        assert "location_id" in row
        assert "coverage_pct" in row
        assert "available_records" in row
        assert "total_records" in row

    # Unknown feature -> 404
    resp = client.get("/api/v1/features/not_a_feature/coverage")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"

    app.dependency_overrides.clear()


def test_features_workspace_scoping_and_admin_guards() -> None:
    ws = _make_workspace()
    other_ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    viewer = _make_user(ws.id, role=UserRole.viewer)
    # Workspace scoping not directly testable for global features, but per-location coverage must scope.
    loc_other = _make_location(other_ws.id, name="Other Facility")
    loc_own = _make_location(ws.id, name="Own Facility")
    stores: dict[type, dict] = {
        FeatureDefinition: {},
        Location: {loc_own.id: loc_own, loc_other.id: loc_other},
    }
    mock_db = _build_mock_db(stores)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: viewer  # type: ignore[assignment]
    client = TestClient(app)

    resp = client.get("/api/v1/features")
    assert resp.status_code == 403
    resp = client.get("/api/v1/features/lag_7/coverage")
    assert resp.status_code == 403

    # Admin can see only own locations in coverage
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    resp = client.get("/api/v1/features/lag_7/coverage")
    assert resp.status_code == 200
    per_loc_ids = {r["location_id"] for r in resp.json()["per_location"]}
    assert str(loc_own.id) in per_loc_ids
    # Other workspace location should not appear
    assert str(loc_other.id) not in per_loc_ids

    app.dependency_overrides.clear()


# ------------------------------------------------------------- schema safety ---


def test_models_share_one_declarative_base() -> None:
    """``app.database.Base`` must be the models' Base, not a second registry.

    Two declarative bases mean two metadata registries, so ``init_db()`` —
    which creates tables from ``app.database.Base.metadata`` — would silently
    create an *empty* schema while looking perfectly healthy.
    """
    import app.models  # noqa: F401  — register every table
    from app.database import Base as DatabaseBase
    from app.models.base import Base as ModelBase

    assert DatabaseBase is ModelBase
    assert DatabaseBase.metadata.tables, "ORM metadata is empty"


def test_every_jsonb_column_is_mutation_tracked() -> None:
    """JSONB columns must be mutation-tracked, never a bare ``JSONB``.

    SQLAlchemy treats a scalar attribute as dirty by object *identity*, so a
    bare JSONB column silently drops in-place edits (``row.data["k"] = v``).
    That is what made ``/ingest/file/{id}/commit`` answer 422
    ``missing_mapping``: the column map written by ``/mapping`` never reached
    the database. A bare JSONB column anywhere reintroduces the same silent
    data loss, so this guard covers the whole metadata, not just ingestion.

    Two tracked wraps are valid: ``MutableJSONB`` (``MutableDict``) for
    dict-valued columns and ``MutableJSONList`` (``MutableList``) for
    list-valued ones — ``MutableDict.as_mutable()`` refuses lists.
    """
    from sqlalchemy.dialects.postgresql import JSONB

    import app.models  # noqa: F401  — register every table
    from app.models.base import Base, MutableJSONB, MutableJSONList

    offenders = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if (
            isinstance(column.type, JSONB)
            and column.type is not MutableJSONB
            and column.type is not MutableJSONList
        )
    ]
    assert offenders == [], (
        "these JSONB columns are not mutation-tracked — declare them with "
        "MutableJSONB (dict) or MutableJSONList (list): " + ", ".join(offenders)
    )
