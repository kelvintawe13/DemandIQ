"""Health endpoint tests."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import _SpaStaticFiles, create_app


def test_health_liveness() -> None:
    """GET /health returns 200 with status ok."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_health_versioned() -> None:
    """GET /api/v1/health returns detailed health."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/health")
    # Without DB the endpoint still returns 200; database field may be 'ok' or 'unavailable'
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_root_returns_api_info() -> None:
    """GET / returns API info with docs link."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert "version" in data
    assert "docs" in data


def _write_dist(root: Path) -> Path:
    """A minimal built-app directory: the shell plus one hashed asset."""
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        "<!doctype html><title>DemandIQ shell</title>", encoding="utf-8"
    )
    (dist / "assets" / "index-abc.js").write_text("console.log('app')", encoding="utf-8")
    return dist


def test_create_app_mounts_the_built_dist_at_app(tmp_path: Path) -> None:
    """The real app factory serves the built shell under ``/app``.

    ``frontend_dist`` points the mount at this fixture instead of the repo's
    ``frontend/dist`` (which may or may not exist on the machine running the
    suite), so the wiring itself — router order included — is what is asserted.
    """
    app = create_app(frontend_dist=_write_dist(tmp_path))
    client = TestClient(app)

    shell = client.get("/app/login", headers={"accept": "text/html"})
    assert shell.status_code == 200
    assert "DemandIQ shell" in shell.text
    # A client route a router also owns nothing of: still the shell, not the 404
    # the API's unified error shape would otherwise produce.
    assert client.get("/app/reports", headers={"accept": "text/html"}).status_code == 200
    # And the API is not shadowed by the mount.
    assert client.get("/health").json()["status"] == "ok"


def test_spa_mount_serves_the_shell_for_client_routes(tmp_path: Path) -> None:
    """A deep link into the built app returns the shell, not a 404.

    ``frontend/dist`` is built for ``/app/`` (vite ``base`` when ``command ==
    'build'``), so the shell's own routes — ``/app/login``, ``/app/reports`` —
    reach the mount as paths that are not files. They have to fall back to
    ``index.html``, or the app could only ever be entered at ``/app/``.
    """
    dist = _write_dist(tmp_path)

    app = FastAPI()
    app.mount("/app", _SpaStaticFiles(directory=str(dist), html=True), name="frontend")
    client = TestClient(app)

    navigation = {"accept": "text/html,application/xhtml+xml"}

    shell = client.get("/app/login", headers=navigation)
    assert shell.status_code == 200
    assert "DemandIQ shell" in shell.text

    # A real file is still served as itself, with the built asset prefix intact.
    asset = client.get("/app/assets/index-abc.js")
    assert asset.status_code == 200
    assert asset.text == "console.log('app')"

    # A missing asset keeps its 404: answering HTML with a 200 would show up as a
    # MIME error instead of the missing file the request actually asked for.
    assert client.get("/app/assets/missing.js").status_code == 404


def test_error_shape_on_404() -> None:
    """Unknown route returns unified error shape."""
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/unknown-route")
    assert resp.status_code == 404
    body = resp.json()
    assert "error" in body
    assert "code" in body["error"]
    assert "message" in body["error"]
    assert "reference" in body["error"]


def test_validation_error_shape() -> None:
    """Validation error returns field and code."""
    app = create_app()
    client = TestClient(app)
    # POST /auth/register with missing fields should 422 with error shape
    resp = client.post("/api/v1/auth/register", json={})
    assert resp.status_code == 422
    body = resp.json()
    assert "error" in body
    assert body["error"]["code"] == "validation_error"
