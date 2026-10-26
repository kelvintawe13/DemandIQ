"""Sources, connectors and api-keys tests — DB-optional via mocked sessions."""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.api_key import ApiKey
from app.models.connector_mapping import ConnectorMapping
from app.models.location import Connector, DemandObservation
from app.models.pipeline import PipelineRun
from app.models.user import User, UserRole
from app.models.workspace import Workspace
from app.routers.sources import _INGESTION_JOB_TYPES


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


def _make_connector(ws_id: uuid.UUID, name: str = "POS Link") -> Connector:
    return Connector(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        type="pos",
        name=name,
        status="available",
        credentials={"location_ids": []},
        last_sync_at=None,
        created_at=datetime.datetime.now(datetime.UTC),
    )


# ---------------------------------------------------------------------------
# Helpers for mocked DB
# ---------------------------------------------------------------------------


def _mock_scalars_for(
    connectors: dict[uuid.UUID, Connector],
    observations: dict[int, DemandObservation] | None = None,
    runs: dict[uuid.UUID, PipelineRun] | None = None,
    mappings: dict[uuid.UUID, ConnectorMapping] | None = None,
    api_keys: dict[uuid.UUID, ApiKey] | None = None,
):  # type: ignore[no-untyped-def]
    if observations is None:
        observations = {}
    if runs is None:
        runs = {}
    if mappings is None:
        mappings = {}
    if api_keys is None:
        api_keys = {}

    def fake_scalars(stmt):  # type: ignore[no-untyped-def]
        txt = str(stmt)

        # Determine table by text fragments
        class _S:
            def all(self) -> list:  # type: ignore[no-untyped-def]
                if "connectors" in txt:
                    return list(connectors.values())
                if "demand_observations" in txt:
                    return list(observations.values())
                if "pipeline_runs" in txt:
                    return list(runs.values())
                if "connector_mappings" in txt:
                    return list(mappings.values())
                if "api_keys" in txt:
                    return list(api_keys.values())
                return []

        class _R:
            def scalars(self) -> _S:  # type: ignore[no-untyped-def]
                return _S()

        return _R().scalars()

    return fake_scalars


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


def test_sources_list_returns_connectors_aggregated() -> None:
    ws = _make_workspace()
    user = _make_user(ws.id)
    conn = _make_connector(ws.id)
    connectors = {conn.id: conn}

    mock_db = MagicMock()
    mock_db.scalars.side_effect = _mock_scalars_for(connectors)
    mock_db.get.side_effect = lambda m, pk: connectors.get(pk) if m == Connector else None  # type: ignore[no-untyped-def]
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    client = TestClient(app)

    resp = client.get("/api/v1/sources")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body
    assert len(body["data"]) == 1
    src = body["data"][0]
    assert src["id"] == str(conn.id)
    assert src["name"] == conn.name
    assert "status" in src
    assert "freshness" in src
    assert "volume" in src
    assert "health" in src
    # An administrator's connector is pullable: no import kind, so the screen
    # keeps offering Sync for it.
    assert src["import_kind"] is None

    app.dependency_overrides.clear()


def test_sources_list_reports_the_import_kind_of_a_channel_it_created() -> None:
    """An import channel is identified by the kind that fills it.

    The row carries other credentials in production; only ``import_kind`` may
    leave the API, so this asserts the value is picked out of the object rather
    than the whole thing being echoed.
    """
    ws = _make_workspace()
    user = _make_user(ws.id)
    conn = _make_connector(ws.id, name="Spreadsheet uploads")
    conn.type = "file"
    conn.credentials = {"import_kind": "file", "secret": "must-not-leak"}
    connectors = {conn.id: conn}

    mock_db = MagicMock()
    mock_db.scalars.side_effect = _mock_scalars_for(connectors)
    mock_db.flush = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    client = TestClient(app)

    resp = client.get("/api/v1/sources")
    assert resp.status_code == 200, resp.text
    src = resp.json()["data"][0]
    assert src["import_kind"] == "file"
    assert "must-not-leak" not in resp.text

    app.dependency_overrides.clear()


def test_sync_history_query_is_limited_to_ingestion_jobs() -> None:
    """The filter has to be in the SQL: the panel lists what it is given.

    ``pipeline_runs`` holds every job type and the scheduler writes
    ``report_generation`` once a minute, so an unfiltered query fills a panel
    called "Sync history" with report renders that say nothing about ingestion.
    """
    ws = _make_workspace()
    user = _make_user(ws.id)
    captured: list[str] = []

    mock_db = MagicMock()

    def fake_scalars(stmt):  # type: ignore[no-untyped-def]
        captured.append(str(stmt))

        class _R:
            def all(self) -> list:  # type: ignore[no-untyped-def]
                return []

        return _R()

    mock_db.scalars.side_effect = fake_scalars

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    client = TestClient(app)

    resp = client.get("/api/v1/sources/sync-history")
    assert resp.status_code == 200, resp.text
    assert captured, "the endpoint never queried pipeline_runs"
    sql = captured[0]
    assert "pipeline_runs.job_type IN" in sql
    # The IN list is passed as a bound parameter, so the values are pinned where
    # they are declared: both halves of the pair that actually move data.
    assert set(_INGESTION_JOB_TYPES) == {"connector_sync", "feature_materialisation"}

    app.dependency_overrides.clear()


def test_sources_requires_auth() -> None:
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/sources")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_sources_sync_triggers_job_stub() -> None:
    ws = _make_workspace()
    user = _make_user(ws.id)
    conn = _make_connector(ws.id)
    # Only an active connector is syncable; the job selects on this status.
    conn.status = "active"
    connectors = {conn.id: conn}
    runs: dict[uuid.UUID, PipelineRun] = {}

    mock_db = MagicMock()

    def fake_add(obj):  # type: ignore[no-untyped-def]
        if isinstance(obj, PipelineRun):
            runs[obj.id] = obj

    mock_db.add.side_effect = fake_add
    mock_db.get.side_effect = lambda m, pk: connectors.get(pk) if m == Connector else None  # type: ignore[no-untyped-def]
    mock_db.scalars.side_effect = _mock_scalars_for(connectors, runs=runs)
    mock_db.flush = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    client = TestClient(app)

    resp = client.post(f"/api/v1/sources/{conn.id}/sync")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "job_id" in body
    assert body["status"] == "queued"
    assert body["source_id"] == str(conn.id)
    assert uuid.UUID(body["job_id"]) in runs

    # Not found for other workspace
    other_ws = _make_workspace()
    other_conn = _make_connector(other_ws.id)
    # get should return None for other ws id lookup when source_id is other_conn
    mock_db.get.side_effect = lambda m, pk: None if m == Connector and pk == other_conn.id else connectors.get(pk) if m == Connector else None  # type: ignore[no-untyped-def]
    resp = client.post(f"/api/v1/sources/{other_conn.id}/sync")
    assert resp.status_code == 404

    app.dependency_overrides.clear()


def test_sources_sync_refuses_a_non_active_connector() -> None:
    """A sync for a paused/available channel is refused, not queued.

    ``_job_connector_sync`` only pulls ``active`` connectors, so queueing a run
    for any other status would settle as ``success`` having done nothing. The
    endpoint must not accept that request in the first place.
    """
    ws = _make_workspace()
    user = _make_user(ws.id)
    conn = _make_connector(ws.id)  # defaults to `available`
    connectors = {conn.id: conn}

    mock_db = MagicMock()
    mock_db.get.side_effect = lambda m, pk: connectors.get(pk) if m == Connector else None  # type: ignore[no-untyped-def]

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    client = TestClient(app)

    resp = client.post(f"/api/v1/sources/{conn.id}/sync")
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error"]["code"] == "not_active"
    assert "available" in body["error"]["message"]
    # Nothing was enqueued.
    mock_db.add.assert_not_called()

    app.dependency_overrides.clear()


def test_sources_sync_history_returns_runs() -> None:
    ws = _make_workspace()
    user = _make_user(ws.id)
    run = PipelineRun(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        job_type="connector_sync",
        status="success",
        rows_processed=120,
        duration_ms=2300,
        detail="manual",
        started_at=datetime.datetime.now(datetime.UTC),
    )
    runs = {run.id: run}

    mock_db = MagicMock()
    mock_db.scalars.side_effect = _mock_scalars_for({}, runs=runs)
    mock_db.get = MagicMock(return_value=None)
    mock_db.flush = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    client = TestClient(app)

    resp = client.get("/api/v1/sources/sync-history")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "data" in body
    assert len(body["data"]) == 1
    assert body["data"][0]["job_type"] == "connector_sync"
    assert "next_cursor" in body
    assert "has_more" in body

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Connectors CRUD + admin guard + mapping + test endpoint
# ---------------------------------------------------------------------------


def test_connectors_admin_guard() -> None:
    ws = _make_workspace()
    viewer = _make_user(ws.id, role=UserRole.viewer)
    mock_db = MagicMock()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: viewer  # type: ignore[assignment]
    client = TestClient(app)

    for method, url, payload in [
        ("GET", "/api/v1/connectors", None),
        ("POST", "/api/v1/connectors", {"type": "pos", "name": "X"}),
    ]:
        if method == "GET":
            resp = client.get(url)
        else:
            resp = client.post(url, json=payload)
        assert resp.status_code == 403, f"{method} {url} should be 403"
        assert resp.json()["error"]["code"] == "forbidden"

    app.dependency_overrides.clear()


def test_connectors_crud_and_test_and_mapping() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    connectors: dict[uuid.UUID, Connector] = {}
    mappings: dict[uuid.UUID, ConnectorMapping] = {}

    mock_db = MagicMock()

    def fake_add(obj):  # type: ignore[no-untyped-def]
        if isinstance(obj, Connector):
            connectors[obj.id] = obj
        elif isinstance(obj, ConnectorMapping):
            mappings[obj.id] = obj

    def fake_get(model, pk):  # type: ignore[no-untyped-def]
        if model == Connector:
            return connectors.get(pk)
        if model == ConnectorMapping:
            return mappings.get(pk)
        return None

    def fake_delete(obj):  # type: ignore[no-untyped-def]
        if isinstance(obj, Connector):
            connectors.pop(obj.id, None)
        elif isinstance(obj, ConnectorMapping):
            mappings.pop(obj.id, None)

    mock_db.add.side_effect = fake_add
    mock_db.get.side_effect = fake_get
    mock_db.delete.side_effect = fake_delete
    mock_db.scalars.side_effect = _mock_scalars_for(connectors, mappings=mappings)
    mock_db.flush = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)

    # Create
    resp = client.post(
        "/api/v1/connectors",
        json={
            "type": "pos",
            "name": "POS A",
            "location_ids": [],
            "credentials": {"api_key": "secret123"},
        },
    )
    assert resp.status_code == 201, resp.text
    cid = resp.json()["id"]
    assert resp.json()["type"] == "pos"
    assert uuid.UUID(cid) in connectors

    # List — credentials masked
    resp = client.get("/api/v1/connectors")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["data"]) == 1
    # api_key should be masked
    creds = resp.json()["data"][0]["credentials"]
    if creds:
        assert creds.get("api_key") == "***" or "api_key" not in creds or creds["api_key"] == "***"

    # Patch
    resp = client.patch(f"/api/v1/connectors/{cid}", json={"name": "POS B", "status": "active"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "POS B"
    assert resp.json()["status"] == "active"

    # Test endpoint
    resp = client.post(f"/api/v1/connectors/{cid}/test")
    assert resp.status_code == 200, resp.text
    assert "latency_ms" in resp.json()
    assert "sample_payload" in resp.json()
    assert isinstance(resp.json()["latency_ms"], int)
    assert "demand_qty" in resp.json()["sample_payload"]

    # Mapping get initially empty
    resp = client.get(f"/api/v1/connectors/{cid}/mapping")
    assert resp.status_code == 200, resp.text
    assert resp.json()["connector_id"] == cid
    assert resp.json()["mappings"] == []

    # Mapping patch
    resp = client.patch(
        f"/api/v1/connectors/{cid}/mapping",
        json={
            "mappings": [
                {"source_field": "qty", "canonical_field": "demand_qty"},
                {"source_field": "dt", "canonical_field": "date"},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["mappings"]) == 2
    assert any(m["canonical_field"] == "demand_qty" for m in resp.json()["mappings"])

    # Mapping get again
    resp = client.get(f"/api/v1/connectors/{cid}/mapping")
    assert resp.status_code == 200
    assert len(resp.json()["mappings"]) == 2

    # Invalid mapping canonical
    resp = client.patch(
        f"/api/v1/connectors/{cid}/mapping",
        json={"mappings": [{"source_field": "x", "canonical_field": "not_valid"}]},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_canonical"

    # Delete
    resp = client.delete(f"/api/v1/connectors/{cid}")
    assert resp.status_code == 204

    # List after delete empty
    resp = client.get("/api/v1/connectors")
    assert len(resp.json()["data"]) == 0

    # After delete, get mapping 404
    resp = client.get(f"/api/v1/connectors/{cid}/mapping")
    assert resp.status_code == 404

    app.dependency_overrides.clear()


def test_connectors_invalid_type() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    mock_db = MagicMock()
    mock_db.scalars.side_effect = _mock_scalars_for({})
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)
    resp = client.post("/api/v1/connectors", json={"type": "invalid", "name": "X"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_type"
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Api Keys — create / rotate / revoke + admin guard
# ---------------------------------------------------------------------------


def test_api_keys_admin_guard() -> None:
    ws = _make_workspace()
    viewer = _make_user(ws.id, role=UserRole.viewer)
    mock_db = MagicMock()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: viewer  # type: ignore[assignment]
    client = TestClient(app)
    resp = client.get("/api/v1/api-keys")
    assert resp.status_code == 403
    resp = client.post("/api/v1/api-keys", json={"label": "k1"})
    assert resp.status_code == 403
    app.dependency_overrides.clear()


def test_api_keys_create_rotate_revoke() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    keys: dict[uuid.UUID, ApiKey] = {}

    mock_db = MagicMock()

    def fake_add(obj):  # type: ignore[no-untyped-def]
        if isinstance(obj, ApiKey):
            keys[obj.id] = obj

    def fake_get(model, pk):  # type: ignore[no-untyped-def]
        if model == ApiKey:
            return keys.get(pk)
        return None

    def fake_scalars(stmt):  # type: ignore[no-untyped-def]
        txt = str(stmt)

        class _S:
            def all(self) -> list:  # type: ignore[no-untyped-def]
                if "api_keys" in txt:
                    return list(keys.values())
                return []

        class _R:
            def scalars(self) -> _S:  # type: ignore[no-untyped-def]
                return _S()

        return _R().scalars()

    mock_db.add.side_effect = fake_add
    mock_db.get.side_effect = fake_get
    mock_db.scalars.side_effect = fake_scalars
    mock_db.flush = MagicMock()
    mock_db.delete = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)

    # Create
    resp = client.post("/api/v1/api-keys", json={"label": "CI Key", "scopes": ["read", "write"]})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "secret" in body
    assert "prefix" in body
    assert body["label"] == "CI Key"
    assert body["prefix"] in body["secret"] or body["secret"].startswith("diq_")
    assert body["scopes"] == ["read", "write"]
    kid = body["id"]
    secret1 = body["secret"]
    prefix1 = body["prefix"]
    # key_hash stored bcrypt not equal secret
    stored = keys[uuid.UUID(kid)]
    assert stored.key_hash != secret1
    assert stored.prefix == prefix1

    # List — secret not returned, prefix visible
    resp = client.get("/api/v1/api-keys")
    assert resp.status_code == 200, resp.text
    lst = resp.json()["data"]
    assert len(lst) == 1
    assert lst[0]["prefix"] == prefix1
    assert "secret" not in lst[0]
    assert lst[0]["last_used_at"] is None
    assert lst[0]["scopes"] == ["read", "write"]

    # Rotate — new secret differs, prefix updated
    resp = client.post(f"/api/v1/api-keys/{kid}/rotate")
    assert resp.status_code == 200, resp.text
    body2 = resp.json()
    assert "secret" in body2
    assert body2["secret"] != secret1
    assert body2["prefix"] != prefix1 or body2["id"] == kid
    assert body2["prefix"] in body2["secret"]
    stored2 = keys[uuid.UUID(kid)]
    assert stored2.key_hash != secret1
    assert stored2.revoked_at is None

    # List after rotate — prefix reflects new
    resp = client.get("/api/v1/api-keys")
    assert resp.json()["data"][0]["prefix"] == body2["prefix"]

    # Revoke (delete)
    resp = client.delete(f"/api/v1/api-keys/{kid}")
    assert resp.status_code == 204
    assert stored2.revoked_at is not None

    # Rotate revoked should 409
    resp = client.post(f"/api/v1/api-keys/{kid}/rotate")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "revoked"

    # List still returns revoked key with revoked_at set
    resp = client.get("/api/v1/api-keys")
    assert resp.json()["data"][0]["revoked_at"] is not None

    app.dependency_overrides.clear()


def test_api_keys_validation() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    mock_db = MagicMock()
    mock_db.scalars.side_effect = _mock_scalars_for({}, api_keys={})
    mock_db.add = MagicMock()
    mock_db.flush = MagicMock()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)
    resp = client.post("/api/v1/api-keys", json={"label": ""})
    assert resp.status_code == 400
    resp = client.post("/api/v1/api-keys", json={"label": "ok", "scopes": []})
    assert resp.status_code == 400
    app.dependency_overrides.clear()


def test_api_keys_not_found_shape() -> None:
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    mock_db = MagicMock()
    mock_db.get.return_value = None
    mock_db.scalars.side_effect = _mock_scalars_for({}, api_keys={})
    mock_db.flush = MagicMock()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app)
    fake = str(uuid.uuid4())
    resp = client.post(f"/api/v1/api-keys/{fake}/rotate")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    resp = client.delete(f"/api/v1/api-keys/{fake}")
    assert resp.status_code == 404
    app.dependency_overrides.clear()


def test_mapping_replacement_aborts_when_the_clear_fails() -> None:
    """Replacing a mapping must not continue after failing to delete the old rows.

    The old behaviour logged the failure and inserted the replacement anyway,
    which is exactly how a connector ends up with duplicate source mappings.
    """
    ws = _make_workspace()
    admin = _make_user(ws.id, role=UserRole.administrator)
    conn = _make_connector(ws.id)
    stale = ConnectorMapping(
        id=uuid.uuid4(),
        connector_id=conn.id,
        source_field="old_qty",
        canonical_field="demand_qty",
        transform=None,
    )
    mappings = {stale.id: stale}
    added: list[ConnectorMapping] = []

    def fake_add(obj):  # type: ignore[no-untyped-def]
        if isinstance(obj, ConnectorMapping):
            added.append(obj)

    mock_db = MagicMock()
    mock_db.get.side_effect = lambda m, pk: conn if m == Connector else None  # type: ignore[no-untyped-def]
    mock_db.scalars.side_effect = _mock_scalars_for({conn.id: conn}, mappings=mappings)
    mock_db.add.side_effect = fake_add
    mock_db.flush.side_effect = RuntimeError("could not delete the existing rows")

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: admin  # type: ignore[assignment]
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.patch(
        f"/api/v1/connectors/{conn.id}/mapping",
        json={"mappings": [{"source_field": "qty", "canonical_field": "demand_qty"}]},
    )

    assert resp.status_code == 500, resp.text
    assert resp.json()["error"]["code"] == "internal_error"
    assert added == [], "no replacement rows may be inserted after a failed clear"
