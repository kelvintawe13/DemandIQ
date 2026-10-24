"""Reports, webhooks and governance tests (phase 10-11).

DB-optional via mocked sessions, same style as other test modules.
"""

from __future__ import annotations

import datetime
import json
import uuid
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.governance import ChangeRequest
from app.models.report import Report, ReportSchedule
from app.models.user import User, UserRole
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.models.workspace import Workspace
from app.security.api_key_auth import get_principal
from app.services.delivery.webhooks import EVENT_IDS, sign_payload, verify_signature
from app.services.reporting.builder import REPORT_TEMPLATES
from app.services.reporting.scheduler import compute_next_run


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


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_report_templates() -> None:
    ws = _make_workspace()
    user = _make_user(ws.id)
    mock_db = MagicMock()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]  # routes via get_principal too

    client = TestClient(app)
    resp = client.get("/api/v1/reports/templates")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body
    assert len(body["data"]) == 4
    ids = {t["id"] for t in body["data"]}
    assert ids == {t["id"] for t in REPORT_TEMPLATES}

    app.dependency_overrides.clear()


def test_report_generate_and_poll() -> None:
    ws = _make_workspace()
    user = _make_user(ws.id)
    # In-memory stores
    reports: dict[uuid.UUID, Report] = {}
    schedules: dict[uuid.UUID, ReportSchedule] = {}

    mock_db = MagicMock()

    def fake_add(obj) -> None:  # type: ignore[no-untyped-def]
        if isinstance(obj, Report):
            reports[obj.id] = obj
        elif isinstance(obj, ReportSchedule):
            schedules[obj.id] = obj

    def fake_get(model, pk):  # type: ignore[no-untyped-def]
        if model == Report:
            return reports.get(pk)
        if model == ReportSchedule:
            return schedules.get(pk)
        return None

    def fake_scalars(stmt):  # type: ignore[no-untyped-def]
        class _S:
            def all(self) -> list:  # type: ignore[no-untyped-def]
                txt = str(stmt)
                if "reports" in txt:
                    return list(reports.values())
                if "report_schedules" in txt:
                    return list(schedules.values())
                return []

        class _R:
            def scalars(self) -> _S:  # type: ignore[no-untyped-def]
                return _S()

        return _R().scalars()

    mock_db.add.side_effect = fake_add
    mock_db.get.side_effect = fake_get
    mock_db.scalars.side_effect = fake_scalars
    mock_db.scalar.return_value = None
    mock_db.flush = MagicMock()
    mock_db.delete.side_effect = lambda obj: reports.pop(obj.id, None) or schedules.pop(obj.id, None)  # type: ignore[no-untyped-def]

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]  # routes via get_principal too

    client = TestClient(app)

    # Generate pdf
    resp = client.post(
        "/api/v1/reports/generate",
        json={
            "template_id": "demand_summary",
            "period": {"start": "2026-01-01", "end": "2026-01-31"},
            "format": "pdf",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "report_id" in body
    rid = body["report_id"]

    # Generation is queued, not stubbed: the row exists, the file does not yet,
    # and no download link is offered until the worker has rendered it — see
    # test_report_render.py for the rendering half of that contract.
    resp = client.get(f"/api/v1/reports/{rid}")
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert detail["id"] == rid
    assert detail["status"] == "queued"
    assert detail["signed_url"] is None
    assert detail["download_url"] is None

    # List with pagination
    resp = client.get("/api/v1/reports?limit=1")
    assert resp.status_code == 200
    lst = resp.json()
    assert "data" in lst
    assert "next_cursor" in lst
    assert "has_more" in lst

    # Invalid template
    resp = client.post("/api/v1/reports/generate", json={"template_id": "nope", "format": "pdf"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_template"

    # Invalid format
    resp = client.post(
        "/api/v1/reports/generate", json={"template_id": "demand_summary", "format": "docx"}
    )
    assert resp.status_code == 400

    app.dependency_overrides.clear()


def test_report_format_variants() -> None:
    ws = _make_workspace()
    user = _make_user(ws.id)
    reports: dict[uuid.UUID, Report] = {}
    mock_db = MagicMock()
    mock_db.add.side_effect = lambda obj: reports.__setitem__(obj.id, obj) if isinstance(obj, Report) else None  # type: ignore[no-untyped-def]
    mock_db.get.side_effect = lambda m, pk: reports.get(pk) if m == Report else None  # type: ignore[no-untyped-def]
    mock_db.scalars.side_effect = lambda stmt: MagicMock(scalars=lambda: MagicMock(all=lambda: list(reports.values())))  # type: ignore[no-untyped-def]
    mock_db.scalar.return_value = None
    mock_db.flush = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]  # routes via get_principal too
    client = TestClient(app)

    for fmt in ("pdf", "csv", "xlsx"):
        resp = client.post(
            "/api/v1/reports/generate", json={"template_id": "forecast_accuracy", "format": fmt}
        )
        assert resp.status_code == 201, f"fmt {fmt} failed: {resp.text}"
        rid = resp.json()["report_id"]
        # Poll
        r = client.get(f"/api/v1/reports/{rid}")
        assert r.status_code == 200
        assert r.json()["format"] == fmt

    app.dependency_overrides.clear()


def test_report_schedules_cadence() -> None:
    ws = _make_workspace()
    user = _make_user(ws.id)
    schedules: dict[uuid.UUID, ReportSchedule] = {}
    mock_db = MagicMock()

    def fake_add(obj) -> None:  # type: ignore[no-untyped-def]
        if isinstance(obj, ReportSchedule):
            schedules[obj.id] = obj

    def fake_scalars(stmt):  # type: ignore[no-untyped-def]
        class _S:
            def all(self) -> list:  # type: ignore[no-untyped-def]
                return list(schedules.values())

        class _R:
            def scalars(self) -> _S:  # type: ignore[no-untyped-def]
                return _S()

        return _R().scalars()

    mock_db.add.side_effect = fake_add
    mock_db.get.side_effect = lambda m, pk: schedules.get(pk) if m == ReportSchedule else None  # type: ignore[no-untyped-def]
    mock_db.scalars.side_effect = fake_scalars
    mock_db.scalar.return_value = None
    mock_db.flush = MagicMock()
    mock_db.delete.side_effect = lambda obj: schedules.pop(obj.id, None)  # type: ignore[no-untyped-def]

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]  # routes via get_principal too
    client = TestClient(app)

    # Create daily
    resp = client.post(
        "/api/v1/reports/schedules",
        json={
            "template": "demand_summary",
            "cadence": "daily",
            "recipients": ["ops@example.com"],
            "format": "pdf",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["cadence"] == "daily"
    assert body["next_run_at"] is not None
    sid = body["id"]

    # List
    resp = client.get("/api/v1/reports/schedules")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 1

    # Patch cadence to weekly — next_run_at should update
    resp = client.patch(f"/api/v1/reports/schedules/{sid}", json={"cadence": "weekly"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["cadence"] == "weekly"

    # Patch enabled false — next_run_at becomes None
    resp = client.patch(f"/api/v1/reports/schedules/{sid}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    assert resp.json()["next_run_at"] is None

    # Delete
    resp = client.delete(f"/api/v1/reports/schedules/{sid}")
    assert resp.status_code == 204

    # List now empty
    resp = client.get("/api/v1/reports/schedules")
    assert len(resp.json()["data"]) == 0

    app.dependency_overrides.clear()


def test_compute_next_run_values() -> None:
    base = datetime.datetime(2026, 3, 15, 10, 0, tzinfo=datetime.UTC)  # Sunday
    daily = compute_next_run("daily", base)
    assert daily > base
    assert daily.hour == 7
    weekly = compute_next_run("weekly", base)
    assert weekly.weekday() == 0  # Monday
    monthly = compute_next_run("monthly", base)
    assert monthly.day == 1
    assert monthly > base


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------


def test_webhook_crud_and_events() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    endpoints: dict[uuid.UUID, WebhookEndpoint] = {}
    deliveries: dict[int, WebhookDelivery] = {}

    mock_db = MagicMock()

    def fake_add(obj) -> None:  # type: ignore[no-untyped-def]
        if isinstance(obj, WebhookEndpoint):
            endpoints[obj.id] = obj
        elif isinstance(obj, WebhookDelivery):
            deliveries[obj.id] = obj

    def fake_scalars(stmt):  # type: ignore[no-untyped-def]
        txt = str(stmt)

        class _S:
            def all(self) -> list:  # type: ignore[no-untyped-def]
                if "webhook_endpoints" in txt:
                    return list(endpoints.values())
                if "webhook_deliveries" in txt:
                    return list(deliveries.values())
                return []

        class _R:
            def scalars(self) -> _S:  # type: ignore[no-untyped-def]
                return _S()

        return _R().scalars()

    mock_db.add.side_effect = fake_add
    mock_db.get.side_effect = lambda m, pk: endpoints.get(pk) if m == WebhookEndpoint else None  # type: ignore[no-untyped-def]
    mock_db.scalars.side_effect = fake_scalars
    mock_db.scalar.return_value = None
    mock_db.flush = MagicMock()
    mock_db.delete.side_effect = lambda obj: endpoints.pop(obj.id, None)  # type: ignore[no-untyped-def]

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: admin  # type: ignore[assignment]  # routes via get_principal too
    client = TestClient(app)

    # Events catalog
    resp = client.get("/api/v1/webhooks/events")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) >= 8
    assert any(e["id"] == "forecast.generated" for e in resp.json()["data"])

    # Create endpoint
    resp = client.post(
        "/api/v1/webhooks",
        json={"url": "https://example.com/hook", "events": ["forecast.generated", "alert.raised"]},
    )
    assert resp.status_code == 201, resp.text
    wid = resp.json()["id"]
    assert resp.json()["secret"]  # returned once

    # List — secret masked
    resp = client.get("/api/v1/webhooks")
    assert resp.status_code == 200
    assert resp.json()["data"][0]["secret"] == "***"

    # Patch
    resp = client.patch(f"/api/v1/webhooks/{wid}", json={"events": ["forecast.generated"]})
    assert resp.status_code == 200
    assert resp.json()["events"] == ["forecast.generated"]

    # Test delivery (dry-run) — should create a delivery log
    resp = client.post(f"/api/v1/webhooks/{wid}/test")
    assert resp.status_code == 200, resp.text
    assert resp.json()["response_code"] == 200
    assert resp.json()["event"] == "forecast.generated"

    # Deliveries log
    resp = client.get(f"/api/v1/webhooks/{wid}/deliveries")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) >= 1
    assert "next_cursor" in resp.json()
    assert "has_more" in resp.json()

    # Delete
    resp = client.delete(f"/api/v1/webhooks/{wid}")
    assert resp.status_code == 204

    # After delete, list empty
    resp = client.get("/api/v1/webhooks")
    assert len(resp.json()["data"]) == 0

    app.dependency_overrides.clear()


def test_webhook_requires_admin() -> None:
    ws = _make_workspace()
    viewer = _make_user(ws.id, role=UserRole.viewer)
    mock_db = MagicMock()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: viewer  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: viewer  # type: ignore[assignment]  # routes via get_principal too
    client = TestClient(app)
    resp = client.get("/api/v1/webhooks")
    assert resp.status_code == 403
    resp = client.post(
        "/api/v1/webhooks",
        json={"url": "https://example.com/hook", "events": ["forecast.generated"]},
    )
    assert resp.status_code == 403
    app.dependency_overrides.clear()


def test_webhook_invalid_event() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    mock_db = MagicMock()
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: admin  # type: ignore[assignment]  # routes via get_principal too
    client = TestClient(app)
    resp = client.post(
        "/api/v1/webhooks", json={"url": "https://example.com/hook", "events": ["not.a.real.event"]}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_event"
    app.dependency_overrides.clear()


def test_webhook_hmac_signing() -> None:
    secret = "test-secret-123"
    body = json.dumps(
        {"event": "forecast.generated", "data": {"x": 1}}, separators=(",", ":"), sort_keys=True
    ).encode()
    sig = sign_payload(secret, body)
    assert sig.startswith("sha256=")
    assert verify_signature(secret, body, sig) is True
    assert verify_signature(secret, body, "sha256=badsig") is False
    # Tampered body should fail
    assert verify_signature(secret, b"tampered", sig) is False
    # Also direct hex without prefix
    hex_only = sig.replace("sha256=", "")
    assert verify_signature(secret, body, hex_only) is True


def test_webhook_deliveries_pagination_and_retry_shape() -> None:
    # Verify that deliveries store attempt counts and retry logs would show 5 attempts on failure.
    # Here we exercise the deliver_webhook function directly with mocked http failure.
    ws = _make_workspace()
    ep = WebhookEndpoint(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        url="https://example.com/fail",
        events=["alert.raised"],
        secret="s3cr3t",
        status="active",
        created_at=datetime.datetime.now(datetime.UTC),
    )
    mock_db = MagicMock()
    deliveries: list[WebhookDelivery] = []

    def fake_add(obj):  # type: ignore[no-untyped-def]
        if isinstance(obj, WebhookDelivery):
            deliveries.append(obj)
            obj.id = len(deliveries)

    mock_db.add.side_effect = fake_add
    mock_db.flush = MagicMock()

    # Simulate HTTP failure by using a client that always returns 500
    import httpx

    def failing_handler(request: httpx.Request) -> httpx.Response:  # type: ignore[no-untyped-def]
        return httpx.Response(500, request=request)

    transport = httpx.MockTransport(failing_handler)
    client = httpx.Client(transport=transport)

    from app.services.delivery.webhooks import deliver_webhook

    result = deliver_webhook(mock_db, ep, "alert.raised", {"msg": "test"}, http_client=client)
    # Should have retried 5 times; last delivery has attempt==5 and code 500
    assert result.attempt == 5
    assert result.response_code == 500
    assert len(deliveries) == 5


# ---------------------------------------------------------------------------
# Governance
# ---------------------------------------------------------------------------


def test_change_requests_two_person_rule() -> None:
    ws = _make_workspace()
    requester = _make_user(ws.id, role=UserRole.administrator)
    decider = _make_user(ws.id, role=UserRole.owner)

    cr_store: dict[uuid.UUID, ChangeRequest] = {}
    mock_db = MagicMock()

    def fake_add(obj) -> None:  # type: ignore[no-untyped-def]
        if isinstance(obj, ChangeRequest):
            cr_store[obj.id] = obj

    mock_db.add.side_effect = fake_add
    mock_db.get.side_effect = lambda m, pk: cr_store.get(pk) if m == ChangeRequest else None  # type: ignore[no-untyped-def]

    def fake_scalars(stmt):  # type: ignore[no-untyped-def]
        class _S:
            def all(self) -> list:  # type: ignore[no-untyped-def]
                return list(cr_store.values())

        class _R:
            def scalars(self) -> _S:  # type: ignore[no-untyped-def]
                return _S()

        return _R().scalars()

    mock_db.scalars.side_effect = fake_scalars
    mock_db.scalar.return_value = None
    mock_db.flush = MagicMock()

    # Need to handle audit log adds — mock_db.add already stores only CR; audit logs ignored fine
    def fake_add_with_audit(obj) -> None:  # type: ignore[no-untyped-def]
        if isinstance(obj, ChangeRequest):
            cr_store[obj.id] = obj
        # audit logs not stored but allowed

    mock_db.add.side_effect = fake_add_with_audit

    app = create_app()
    # Requester creates CR
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: requester  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: requester  # type: ignore[assignment]  # routes via get_principal too
    client = TestClient(app)

    resp = client.post(
        "/api/v1/change-requests",
        json={
            "kind": "policy_change",
            "title": "Raise quantile to 0.90",
            "before_value": "0.80",
            "after_value": "0.90",
        },
    )
    assert resp.status_code == 201, resp.text
    cr_id = resp.json()["id"]

    # Requester tries to approve own request -> 422 two_person_rule
    resp = client.post(f"/api/v1/change-requests/{cr_id}/approve")
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "two_person_rule"

    # Different admin approves -> success
    app.dependency_overrides[get_current_user] = lambda: decider  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: decider  # type: ignore[assignment]  # routes via get_principal too
    resp = client.post(f"/api/v1/change-requests/{cr_id}/approve")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"

    # Already decided — second approve should 422 already_decided
    resp = client.post(f"/api/v1/change-requests/{cr_id}/approve")
    assert resp.status_code == 422
    assert (
        "already_decided" in resp.json()["error"]["code"]
        or "already" in resp.json()["error"]["message"].lower()
    )

    # GET list
    resp = client.get("/api/v1/change-requests")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) >= 1

    app.dependency_overrides.clear()


def test_change_requests_reject_flow() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.owner)
    other = _make_user(ws.id, role=UserRole.administrator)
    cr_store: dict[uuid.UUID, ChangeRequest] = {}
    mock_db = MagicMock()
    mock_db.add.side_effect = lambda obj: cr_store.__setitem__(obj.id, obj) if isinstance(obj, ChangeRequest) else None  # type: ignore[no-untyped-def]
    mock_db.get.side_effect = lambda m, pk: cr_store.get(pk) if m == ChangeRequest else None  # type: ignore[no-untyped-def]
    mock_db.scalars.side_effect = lambda stmt: MagicMock(scalars=lambda: MagicMock(all=lambda: list(cr_store.values())))  # type: ignore[no-untyped-def]
    mock_db.flush = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: admin  # type: ignore[assignment]  # routes via get_principal too
    client = TestClient(app)

    resp = client.post(
        "/api/v1/change-requests",
        json={
            "kind": "access_grant",
            "title": "Grant analyst to X",
            "before_value": "viewer",
            "after_value": "analyst",
        },
    )
    assert resp.status_code == 201
    cr_id = resp.json()["id"]

    app.dependency_overrides[get_current_user] = lambda: other  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: other  # type: ignore[assignment]  # routes via get_principal too
    resp = client.post(f"/api/v1/change-requests/{cr_id}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"

    app.dependency_overrides.clear()


def test_members_and_roles_and_pipeline() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    mock_db = MagicMock()
    # For list_members, need to return [admin]
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[admin]))
    mock_db.scalar.return_value = None
    mock_db.get.side_effect = lambda m, pk: admin if m == User and pk == admin.id else None  # type: ignore[no-untyped-def]
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()
    mock_db.delete = MagicMock()
    # For invite, need to check existing email not found, scalar returns None
    mock_db.execute = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: admin  # type: ignore[assignment]  # routes via get_principal too
    client = TestClient(app)

    # Members list
    resp = client.get("/api/v1/members")
    assert resp.status_code == 200
    assert "data" in resp.json()

    # Roles
    resp = client.get("/api/v1/roles")
    assert resp.status_code == 200
    assert any(r["role"] == "owner" for r in resp.json()["data"])

    # Pipeline runs
    resp = client.get("/api/v1/pipeline/runs")
    assert resp.status_code == 200
    assert "data" in resp.json()

    # Pipeline drift
    resp = client.get("/api/v1/pipeline/drift")
    assert resp.status_code == 200
    assert "signals" in resp.json()

    # Pipeline schedule
    resp = client.get("/api/v1/pipeline/schedule")
    assert resp.status_code == 200
    assert "data" in resp.json()

    # Manual job trigger (admin) — runs the real job and records a run row
    resp = client.post("/api/v1/pipeline/jobs/drift_check/run")
    assert resp.status_code == 200, resp.text
    assert resp.json()["job_type"] == "drift_check"
    assert "status" in resp.json()

    # Unknown job type is a clean 404
    resp = client.post("/api/v1/pipeline/jobs/nope/run")
    assert resp.status_code == 404

    # Audit log (from governance or access)
    # Mock audit log scalars
    mock_db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    resp = client.get("/api/v1/audit-log")
    assert resp.status_code == 200

    app.dependency_overrides.clear()


def test_webhook_events_catalog_includes_all_required() -> None:
    # Ensure the required 8 events are present
    required = {
        "forecast.generated",
        "forecast.updated",
        "policy.changed",
        "alert.raised",
        "alert.resolved",
        "ingest.completed",
        "ingest.failed",
        "model.promoted",
    }
    assert required.issubset(EVENT_IDS)
