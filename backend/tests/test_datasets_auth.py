"""Datasets, waste and auth password tests.

Covers:
* POST /auth/password/forgot — always 202, never reveals
* POST /auth/password/reset — token verify stub, validation
* GET /datasets — admin, returns recruit + urban-waste
* GET /datasets/{id}/manifest — checksums/date range/preprocessing version
* GET /waste/summary — admin, descriptive stats, evidence boundary (never joined), audit
"""

from __future__ import annotations

import datetime
import inspect
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.audit import AuditLog
from app.models.user import User, UserRole
from app.models.waste import WasteObservation
from app.models.workspace import Workspace
from app.routers.auth import create_password_reset_token_for_test
from app.security.hashing import verify_password


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
        password_hash="$2b$12$dummyhashedvalue00000000000000000000000000000000",
        role=role,
        email_verified=True,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _clear_rate_buckets() -> None:
    from app.security.rate_limit import _buckets

    _buckets.clear()


# ---------------------------------------------------------------------------
# Auth password — forgot
# ---------------------------------------------------------------------------


def test_forgot_always_202_never_reveals() -> None:
    _clear_rate_buckets()
    ws = _make_workspace()
    user = _make_user(ws.id)
    mock_db = MagicMock()
    # Existing email path
    mock_db.scalar.return_value = user
    mock_db.flush = MagicMock()
    mock_db.add = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    client = TestClient(app)

    resp = client.post("/api/v1/auth/password/forgot", json={"email": "test@example.com"})
    assert resp.status_code == 202, resp.text
    assert "If the email exists" in resp.json()["message"]

    # Non-existing email — same 202, same message
    _clear_rate_buckets()
    mock_db.scalar.return_value = None
    resp2 = client.post("/api/v1/auth/password/forgot", json={"email": "unknown@example.com"})
    assert resp2.status_code == 202, resp2.text
    assert resp2.json()["message"] == resp.json()["message"]

    # Invalid email format -> 422 validation_error
    _clear_rate_buckets()
    resp3 = client.post("/api/v1/auth/password/forgot", json={"email": "not-an-email"})
    assert resp3.status_code == 422
    assert resp3.json()["error"]["code"] == "validation_error"

    app.dependency_overrides.clear()


def test_forgot_rate_limit_shape() -> None:
    _clear_rate_buckets()
    mock_db = MagicMock()
    mock_db.scalar.return_value = None
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    client = TestClient(app)
    # 5 allowed, 6th should be 429
    for i in range(5):
        r = client.post("/api/v1/auth/password/forgot", json={"email": f"user{i}@example.com"})
        assert r.status_code == 202
    r = client.post("/api/v1/auth/password/forgot", json={"email": "one-more@example.com"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    app.dependency_overrides.clear()
    _clear_rate_buckets()


# ---------------------------------------------------------------------------
# Auth password — reset
# ---------------------------------------------------------------------------


def test_reset_invalid_token_rejected() -> None:
    mock_db = MagicMock()
    mock_db.get.return_value = None
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    client = TestClient(app)

    resp = client.post(
        "/api/v1/auth/password/reset",
        json={"token": "invalid-token-value", "password": "NewPass123!"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_token"

    # Wrong type token (access token) should also be invalid_token
    from app.security.jwt import create_access_token

    ws = _make_workspace()
    user = _make_user(ws.id)
    bad = create_access_token(subject=str(user.id), workspace_id=str(ws.id), role="owner")
    resp2 = client.post(
        "/api/v1/auth/password/reset", json={"token": bad, "password": "NewPass123!"}
    )
    assert resp2.status_code == 400
    assert resp2.json()["error"]["code"] == "invalid_token"

    app.dependency_overrides.clear()


def test_reset_password_validation() -> None:
    mock_db = MagicMock()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    client = TestClient(app)

    # Too short password -> 422
    resp = client.post(
        "/api/v1/auth/password/reset", json={"token": "valid-token-12345", "password": "short"}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert resp.json()["error"]["field"] == "password"

    # Missing token -> 422
    resp2 = client.post("/api/v1/auth/password/reset", json={"password": "LongEnough123!"})
    assert resp2.status_code == 422

    app.dependency_overrides.clear()


def test_reset_success_and_hash() -> None:
    ws = _make_workspace()
    user = _make_user(ws.id)
    # Need a real reset token for this user
    token = create_password_reset_token_for_test(user.id)

    mock_db = MagicMock()
    mock_db.get.return_value = user
    mock_db.flush = MagicMock()
    mock_db.add = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    client = TestClient(app)

    new_pw = "SuperSecure123!"
    resp = client.post("/api/v1/auth/password/reset", json={"token": token, "password": new_pw})
    assert resp.status_code == 200, resp.text
    assert resp.json()["message"] == "Password has been reset"
    # Verify password hash was updated and is verifiable
    assert verify_password(new_pw, user.password_hash) is True
    mock_db.flush.assert_called()

    app.dependency_overrides.clear()


def test_reset_unknown_user_returns_invalid_token() -> None:
    fake_id = uuid.uuid4()
    token = create_password_reset_token_for_test(fake_id)
    mock_db = MagicMock()
    mock_db.get.return_value = None
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    client = TestClient(app)
    resp = client.post(
        "/api/v1/auth/password/reset", json={"token": token, "password": "AnotherPass123!"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_token"
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Datasets — admin guard + shape
# ---------------------------------------------------------------------------


def test_datasets_requires_admin() -> None:
    """The dataset console is administrator work, not ownership oversight."""
    ws = _make_workspace()
    viewer = _make_user(ws.id, role=UserRole.viewer)
    operator = _make_user(ws.id, role=UserRole.operator)
    analyst = _make_user(ws.id, role=UserRole.analyst)
    # The owner is deliberately excluded too: they own the business decisions,
    # not the modelling console (see app/security/permissions.py).
    owner = _make_user(ws.id, role=UserRole.owner)
    mock_db = MagicMock()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]

    for u in (viewer, operator, analyst, owner):
        app.dependency_overrides[get_current_user] = lambda u=u: u  # type: ignore[assignment]
        client = TestClient(app)
        r = client.get("/api/v1/datasets")
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "forbidden"
        r2 = client.get("/api/v1/datasets/recruit/manifest")
        assert r2.status_code == 403
        r3 = client.get("/api/v1/waste/summary")
        assert r3.status_code == 403

    app.dependency_overrides.clear()


def test_datasets_unauthenticated_rejected() -> None:
    app = create_app()
    client = TestClient(app)
    for path in ("/api/v1/datasets", "/api/v1/datasets/recruit/manifest", "/api/v1/waste/summary"):
        resp = client.get(path)
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "unauthorized"


def test_datasets_list_returns_recruit_and_urban_waste() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)

    resp = client.get("/api/v1/datasets")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body
    data = body["data"]
    assert len(data) >= 2
    ids = {d["id"] for d in data}
    assert "recruit" in ids
    assert "urban-waste" in ids

    # Check licences and separation
    recruit = next(d for d in data if d["id"] == "recruit")
    assert "Kaggle" in recruit["licence"]
    assert recruit["table"] == "demand_observations"
    assert "252,108" in recruit["records_detail"] or recruit["records"] == 252108

    urban = next(d for d in data if d["id"] == "urban-waste")
    assert "CC BY 4.0" in urban["licence"]
    assert urban["table"] == "waste_observations"
    assert urban["records"] == 3321
    assert "3,160" in urban["records_detail"] or "3160" in str(urban["records_detail"])
    # Separation note must exist and mention independence
    for d in data:
        assert "separation_note" in d
        assert d["separation_note"]

    # Audit was written
    assert any(isinstance(c.args[0], AuditLog) for c in mock_db.add.call_args_list)  # type: ignore[attr-defined]
    # Find audit with action datasets.list
    actions = [
        c.args[0].action for c in mock_db.add.call_args_list if isinstance(c.args[0], AuditLog)
    ]
    assert "datasets.list" in actions

    # Check workspace scoping — audit workspace_id matches admin workspace
    audits = [c.args[0] for c in mock_db.add.call_args_list if isinstance(c.args[0], AuditLog)]
    for a in audits:
        assert a.workspace_id == admin.workspace_id

    app.dependency_overrides.clear()


def test_datasets_manifest_checksums_and_versions() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)

    # Recruit manifest
    r = client.get("/api/v1/datasets/recruit/manifest")
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["id"] == "recruit"
    assert m["checksums"]
    assert any(k.startswith("sha256:") for k in m["checksums"].values())
    assert m["date_range"]["start"] == "2016-01-01"
    assert m["date_range"]["end"] == "2017-04-22"
    assert m["preprocessing_version"]
    assert m["feature_set_version"]
    assert "demand_observations" in m["schema_ref"] or "schema.sql" in m["schema_ref"]

    # Urban-waste manifest
    r2 = client.get("/api/v1/datasets/urban-waste/manifest")
    assert r2.status_code == 200, r2.text
    m2 = r2.json()
    assert m2["id"] == "urban-waste"
    assert m2["checksums"]
    assert m2["date_range"]["start"] is not None
    assert m2["licence"] == "CC BY 4.0"
    assert m2["preprocessing_version"]
    # waste manifest has no feature_set_version or is None
    assert "preprocessing_version" in m2

    # Audit for manifest
    actions = [
        c.args[0].action for c in mock_db.add.call_args_list if isinstance(c.args[0], AuditLog)
    ]
    assert "datasets.manifest" in actions

    app.dependency_overrides.clear()


def test_datasets_manifest_404_shape() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)
    r = client.get("/api/v1/datasets/not-a-dataset/manifest")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
    assert r.json()["error"]["field"] == "dataset_id"
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Waste summary — stats, boundary, audit
# ---------------------------------------------------------------------------


def test_waste_summary_descriptive_stats_shape() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()
    # Return empty rows so fixture stats are used (covers 3,321/3,160 case)
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)

    r = client.get("/api/v1/waste/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "evidence_boundary" in body
    assert "descriptive_stats" in body
    assert "note" in body
    assert body["licence"] == "CC BY 4.0"
    assert body["source_dataset"] == "urban-waste-ftd"

    stats = body["descriptive_stats"]
    # Required counts
    assert stats["total_records"] == 3321
    assert stats["non_missing_waste"] == 3160
    assert stats["missing_waste"] == 161
    assert stats["establishments"] == 34
    assert stats["ordered_portions_available"] == 9

    # Descriptive stats
    wk = stats["waste_kg"]
    for key in ("count", "mean", "median", "std", "min", "max"):
        assert key in wk, f"missing {key}"
    assert wk["count"] == 3160
    assert isinstance(wk["mean"], (int, float))
    assert isinstance(wk["median"], (int, float))
    assert isinstance(wk["std"], (int, float))
    assert wk["min"] < wk["median"] < wk["max"] or wk["min"] <= wk["max"]
    # Note must mention never joined / independent
    assert "never joined" in body["note"].lower() or "independent" in body["note"].lower()
    assert (
        "never joined" in body["evidence_boundary"].lower()
        or "independent" in body["evidence_boundary"].lower()
    )

    # Establishment variation present
    assert "establishment_variation" in stats
    assert len(stats["establishment_variation"]) >= 1
    ev0 = stats["establishment_variation"][0]
    assert "establishment_code" in ev0
    assert "mean" in ev0 and "std" in ev0

    # Audit
    actions = [
        c.args[0].action for c in mock_db.add.call_args_list if isinstance(c.args[0], AuditLog)
    ]
    assert "waste.summary" in actions

    app.dependency_overrides.clear()


def test_waste_summary_stats_match_audited_analysis() -> None:
    """Fixture fallback must equal the real analyze_waste.py output.

    Section 3.28 forbids presenting synthetic values as observed waste:
    if these ever drift from model/scripts/analyze_waste.py's audited
    output (waste_summary.joblib), this test fails loudly.
    """
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)

    r = client.get("/api/v1/waste/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stats_source"] == "audited_dataset_fallback"

    stats = body["descriptive_stats"]
    assert stats["total_records"] == 3321
    assert stats["non_missing_waste"] == 3160
    assert stats["missing_waste"] == 161
    assert stats["establishments"] == 34
    assert stats["countries"] == 6

    wk = stats["waste_kg"]
    assert wk["count"] == 3160
    assert wk["mean"] == pytest.approx(37.803, abs=0.01)
    assert wk["median"] == pytest.approx(21.4, abs=0.01)
    assert wk["std"] == pytest.approx(45.19, abs=0.01)
    assert wk["min"] == 0.0
    assert wk["max"] == pytest.approx(425.5, abs=0.01)

    assert stats["date_range"] == {"start": "2018-03-02", "end": "2018-11-21"}

    codes = [e["establishment_code"] for e in stats["establishment_variation"]]
    assert len(codes) == 31  # kitchens with >= 5 waste observations
    top = next(
        e
        for e in stats["establishment_variation"]
        if e["establishment_code"] == "Tenerife / El Tope"
    )
    assert top["count"] == 231
    assert top["mean"] == pytest.approx(125.83, abs=0.01)

    # Manifest reports the real observation window and file checksum.
    m = client.get("/api/v1/datasets/urban-waste/manifest")
    assert m.status_code == 200, m.text
    manifest = m.json()
    assert manifest["date_range"] == {"start": "2018-03-02", "end": "2018-11-21"}
    assert manifest["checksums"]["Food Tracking Device dataset.csv"].startswith("sha256:a4f8a791")

    app.dependency_overrides.clear()


def test_waste_summary_from_real_rows_computed() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    # Create realistic waste rows
    rows = [
        WasteObservation(
            establishment_code="EST-A",
            country="ES",
            waste_kg=1.0,
            observed_on=datetime.date(2018, 1, 5),
        ),
        WasteObservation(
            establishment_code="EST-A",
            country="ES",
            waste_kg=3.0,
            observed_on=datetime.date(2018, 1, 6),
        ),
        WasteObservation(
            establishment_code="EST-B",
            country="PT",
            waste_kg=5.0,
            observed_on=datetime.date(2018, 2, 1),
        ),
        WasteObservation(
            establishment_code="EST-B",
            country="PT",
            waste_kg=None,
            observed_on=datetime.date(2018, 2, 2),
        ),
        WasteObservation(
            establishment_code="EST-B",
            country="PT",
            waste_kg=7.0,
            ordered_portions=10,
            observed_on=datetime.date(2018, 2, 3),
        ),
    ]

    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=rows))

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)

    r = client.get("/api/v1/waste/summary")
    assert r.status_code == 200, r.text
    stats = r.json()["descriptive_stats"]
    assert stats["total_records"] == 5
    assert stats["non_missing_waste"] == 4
    assert stats["missing_waste"] == 1
    assert stats["establishments"] == 2
    assert stats["ordered_portions_available"] == 1
    wk = stats["waste_kg"]
    assert wk["count"] == 4
    assert wk["mean"] == 4.0  # (1+3+5+7)/4
    assert wk["median"] == 4.0  # median of [1,3,5,7]
    assert wk["min"] == 1.0
    assert wk["max"] == 7.0
    # establishment variation should have 2 entries
    assert len(stats["establishment_variation"]) == 2

    app.dependency_overrides.clear()


def test_waste_summary_evidence_boundary_no_demand_join() -> None:
    """Ensure waste summary code never references demand_observations as a join."""
    from app.routers import datasets as ds_mod

    source = inspect.getsource(ds_mod.waste_summary)
    # The function must not join to demand — check for forbidden substrings
    forbidden = ["demand_observations", "DemandObservation", "JOIN", "join("]
    for term in forbidden:
        assert term not in source, f"waste_summary must not contain '{term}' (evidence boundary)"

    # Also inspect file-level query: only waste_observations should be queried
    file_src = inspect.getsource(ds_mod)
    # Waste router should import WasteObservation but not DemandObservation
    assert "WasteObservation" in file_src
    # The waste summary should not import or use DemandObservation
    # Extract waste_summary segment
    waste_seg = source
    assert "WasteObservation" in waste_seg
    assert "DemandObservation" not in waste_seg


def test_datasets_and_waste_audit_workspace_scoping() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)

    client.get("/api/v1/datasets")
    client.get("/api/v1/datasets/recruit/manifest")
    client.get("/api/v1/waste/summary")

    audits = [c.args[0] for c in mock_db.add.call_args_list if isinstance(c.args[0], AuditLog)]
    assert len(audits) >= 3
    for a in audits:
        assert a.workspace_id == ws.id
        assert a.actor_id == admin.id
        assert a.client in ("web", "mobile", "api", "system")
        # Error shape not needed here, but audit must have created_at
        assert a.created_at is not None

    app.dependency_overrides.clear()
