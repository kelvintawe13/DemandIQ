"""Auth security tests: silent-refresh contract, Turnstile gating, Google OAuth.

Covers:
* ``POST /auth/refresh`` — happy path (new access token) and revoked-session
  rejection (what the SPA's silent refresh depends on).
* ``verify_turnstile`` — dev skip when no secret; required/rejected/failure
  modes when one is configured (siteverify HTTP is patched).
* ``POST /auth/oauth/google`` — signature validation is exercised in the
  live E2E with a locally signed RSA token; here we verify the provisioning
  and linkage logic by patching token verification.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import get_db
from app.main import create_app
from app.models.user import User, UserRole
from app.routers.auth import _hash_token
from app.schemas.user import GoogleOAuthRequest, LoginRequest
from app.security.human import GoogleIdentity, verify_turnstile


def _clear_rate_buckets() -> None:
    from app.security.rate_limit import _buckets

    _buckets.clear()


def _clear_mfa_challenges() -> None:
    from app.routers import auth as auth_router

    auth_router._mfa_challenges.clear()


def _make_workspace(ws_id: uuid.UUID | None = None) -> MagicMock:
    ws = MagicMock()
    ws.id = ws_id or uuid.uuid4()
    return ws


def _make_user(
    ws_id: uuid.UUID,
    *,
    email: str = "owner@example.com",
    role: UserRole = UserRole.owner,
    mfa_enabled: bool = False,
    deactivated: bool = False,
) -> MagicMock:
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.workspace_id = ws_id
    user.name = "Owner"
    user.email = email
    user.role = role
    user.mfa_enabled = mfa_enabled
    user.mfa_secret = None
    user.deactivated_at = None if not deactivated else object()
    user.password_hash = "x"
    user.avatar_url = None
    user.email_verified = True
    user.phone = None
    user.job_title = None
    return user


# ---------------------------------------------------------------------------
# Refresh — the SPA's silent-refresh contract
# ---------------------------------------------------------------------------


def test_refresh_returns_new_access_token() -> None:
    _clear_rate_buckets()
    ws = _make_workspace()
    user = _make_user(ws.id)

    session = MagicMock()
    session.refresh_hash = None
    session.revoked_at = None

    def scalar_side_effect(stmt):
        model = str(stmt)
        if "sessions" in model.lower():
            return session
        return user

    mock_db = MagicMock()
    mock_db.scalar.side_effect = scalar_side_effect
    mock_db.get.return_value = user

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    client = TestClient(app)

    from app.security.jwt import create_refresh_token

    rt = create_refresh_token(subject=str(user.id))
    session.refresh_hash = _hash_token(rt)

    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": rt})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == get_settings().jwt_ttl_minutes * 60

    app.dependency_overrides.clear()


def test_refresh_rejects_revoked_session() -> None:
    _clear_rate_buckets()
    mock_db = MagicMock()
    mock_db.scalar.return_value = None  # no live session for the hash

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    client = TestClient(app)

    from app.security.jwt import create_refresh_token

    rt = create_refresh_token(subject=str(uuid.uuid4()))
    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": rt})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"

    app.dependency_overrides.clear()


def test_login_and_register_accept_turnstile_token_field() -> None:
    """The schemas carry ``turnstile_token`` (None in dev) end to end."""
    assert LoginRequest(email="a@b.com", password="x").turnstile_token is None
    assert LoginRequest(email="a@b.com", password="x", turnstile_token="t").turnstile_token == "t"


# ---------------------------------------------------------------------------
# Turnstile
# ---------------------------------------------------------------------------


def test_turnstile_skipped_when_secret_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "turnstile_secret", None)
    # No token, no secret → dev pass-through.
    verify_turnstile(None)
    verify_turnstile("some-token")


def test_turnstile_required_when_secret_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    monkeypatch.setattr(get_settings(), "turnstile_secret", "secret")
    with pytest.raises(HTTPException) as exc:
        verify_turnstile(None)
    assert exc.value.status_code == 401
    assert exc.value.detail["error"]["code"] == "captcha_required"


def _fake_siteverify(monkeypatch: pytest.MonkeyPatch, success: bool) -> None:
    payload = (
        {"success": success}
        if success
        else {"success": False, "error-codes": ["invalid-input-response"]}
    )

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self) -> bytes:
            return json.dumps(payload).encode()

    monkeypatch.setattr(
        "app.security.human.urllib.request.urlopen", lambda *a, **k: FakeResp()  # type: ignore[attr-defined]
    )


def test_turnstile_accepts_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "turnstile_secret", "secret")
    _fake_siteverify(monkeypatch, success=True)
    verify_turnstile("good-token", remote_ip="1.2.3.4")


def test_turnstile_rejects_failed_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    monkeypatch.setattr(get_settings(), "turnstile_secret", "secret")
    _fake_siteverify(monkeypatch, success=False)
    with pytest.raises(HTTPException) as exc:
        verify_turnstile("bad-token")
    assert exc.value.status_code == 401
    assert exc.value.detail["error"]["code"] == "captcha_failed"


def test_turnstile_fails_closed_when_siteverify_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    monkeypatch.setattr(get_settings(), "turnstile_secret", "secret")

    def boom(*a, **k):
        raise OSError("network down")

    monkeypatch.setattr("app.security.human.urllib.request.urlopen", boom)  # type: ignore[attr-defined]
    with pytest.raises(HTTPException) as exc:
        verify_turnstile("token")
    assert exc.value.status_code == 503
    assert exc.value.detail["error"]["code"] == "captcha_unavailable"


# ---------------------------------------------------------------------------
# Google OAuth — provisioning / linkage (token crypto covered by E2E)
# ---------------------------------------------------------------------------


def _identity(email: str, subject: str = "google-sub-123") -> GoogleIdentity:
    return GoogleIdentity(
        subject=subject,
        email=email,
        email_verified=True,
        name="Aline Umutoni",
        picture="https://lh3.googleusercontent.com/a.jpg",
        audience="client-id",
    )


def _google_db_mock(user: MagicMock | None, link: MagicMock | None) -> MagicMock:
    mock_db = MagicMock()

    def scalar_side_effect(stmt):
        model = str(stmt).lower()
        if "user_identities" in model:
            return link
        return user

    mock_db.scalar.side_effect = scalar_side_effect
    mock_db.get.return_value = user
    mock_db.add = MagicMock()

    def _flush() -> None:
        # Stand in for the ORM's flush: apply the Python-side column defaults
        # the real session would (uuid PKs, boolean/datetime defaults).
        for call in mock_db.add.call_args_list:
            obj = call.args[0] if call.args else None
            if obj is None:
                continue
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            if hasattr(obj, "mfa_enabled") and obj.mfa_enabled is None:
                obj.mfa_enabled = False
            if hasattr(obj, "created_at") and obj.created_at is None:
                obj.created_at = datetime.now(UTC)

    mock_db.flush = MagicMock(side_effect=_flush)
    return mock_db


def test_google_login_existing_link(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_rate_buckets()
    _clear_mfa_challenges()
    ws = _make_workspace()
    user = _make_user(ws.id, email="owner@example.com")
    user.role = UserRole.owner
    link = MagicMock()
    link.user_id = user.id

    mock_db = _google_db_mock(user, link)

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    client = TestClient(app)

    with patch("app.routers.auth.verify_google_id_token", return_value=_identity(user.email)):
        resp = client.post("/api/v1/auth/oauth/google", json={"id_token": "x" * 20})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["user"]["email"] == user.email
    mock_db.add.assert_called()  # a UserSession was persisted

    app.dependency_overrides.clear()


def test_google_first_signin_links_by_email(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_rate_buckets()
    _clear_mfa_challenges()
    ws = _make_workspace()
    user = _make_user(ws.id, email="owner@example.com")
    user.role = UserRole.owner

    mock_db = _google_db_mock(user, link=None)

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    client = TestClient(app)

    with patch("app.routers.auth.verify_google_id_token", return_value=_identity(user.email)):
        resp = client.post("/api/v1/auth/oauth/google", json={"id_token": "y" * 20})

    assert resp.status_code == 200, resp.text
    added = mock_db.add.call_args_list
    assert any(
        "UserIdentity" in type(c.args[0]).__name__ for c in added if c.args
    ), "identity link not created"

    app.dependency_overrides.clear()


def test_google_signup_provisions_workspace_and_user(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_rate_buckets()
    _clear_mfa_challenges()

    mock_db = _google_db_mock(user=None, link=None)

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    client = TestClient(app)

    ident = _identity("newperson@example.com")
    with patch("app.routers.auth.verify_google_id_token", return_value=ident):
        resp = client.post("/api/v1/auth/oauth/google", json={"id_token": "z" * 20})

    assert resp.status_code == 200, resp.text
    added_types = [type(c.args[0]).__name__ for c in mock_db.add.call_args_list if c.args]
    assert "Workspace" in added_types
    assert "User" in added_types
    assert "UserIdentity" in added_types
    # Fresh Google sign-ups own the workspace they create; the profile gate
    # still applies so they complete name/job title before using the app.
    body = resp.json()
    assert body["user"]["role"] == "owner"
    assert body["needs_profile"] is True

    app.dependency_overrides.clear()


def test_google_rejects_unverified_email(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_rate_buckets()
    mock_db = _google_db_mock(user=None, link=None)

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    client = TestClient(app)

    ident = GoogleIdentity(
        subject="s",
        email="unverified@example.com",
        email_verified=False,
        name=None,
        picture=None,
        audience="client-id",
    )
    with patch("app.routers.auth.verify_google_id_token", return_value=ident):
        resp = client.post("/api/v1/auth/oauth/google", json={"id_token": "q" * 20})

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "oauth_failed"

    app.dependency_overrides.clear()


def test_google_requires_min_token_length() -> None:
    _clear_rate_buckets()
    mock_db = _google_db_mock(user=None, link=None)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    client = TestClient(app)
    resp = client.post("/api/v1/auth/oauth/google", json={"id_token": "short"})
    assert resp.status_code == 422
    app.dependency_overrides.clear()


def test_google_request_schema_shape() -> None:
    req = GoogleOAuthRequest(id_token="a" * 30)
    assert req.id_token == "a" * 30


# ---------------------------------------------------------------------------
# Refresh-token rotation — a refresh token is single-use
# ---------------------------------------------------------------------------


def test_refresh_rotates_token_and_revokes_old_session() -> None:
    """Refresh must revoke the presented session, mint a new one, return it."""
    _clear_rate_buckets()
    ws = _make_workspace()
    user = _make_user(ws.id)

    added_sessions: list[MagicMock] = []
    old_session = MagicMock()
    old_session.refresh_hash = None
    old_session.revoked_at = None
    old_session.device = "ua-test"
    old_session.origin = "127.0.0.1"
    old_session.trusted_device = False

    def scalar_side_effect(stmt):
        # UserSession lives in the `sessions` table; User lookups (via scalar
        # in verify-email) hit `users` and must return the user mock.
        return old_session if "sessions" in str(stmt).lower() else user

    mock_db = MagicMock()
    mock_db.scalar.side_effect = scalar_side_effect
    mock_db.get.return_value = user
    mock_db.add.side_effect = added_sessions.append

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    client = TestClient(app)

    from app.security.jwt import create_refresh_token

    rt = create_refresh_token(subject=str(user.id))
    old_session.refresh_hash = _hash_token(rt)

    resp = client.post("/api/v1/auth/refresh", json={"refresh_token": rt})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"], "rotation must return a fresh refresh token"
    assert body["refresh_token"] != rt, "rotated token must differ from the presented one"
    # old session revoked, one new session provisioned
    assert old_session.revoked_at is not None
    assert len(added_sessions) == 1
    assert added_sessions[0].refresh_hash == _hash_token(body["refresh_token"])

    app.dependency_overrides.clear()


def test_verify_email_accepts_six_digits_in_dev() -> None:
    """Dev convenience: any well-formed 6-digit code verifies (no code store)."""
    _clear_rate_buckets()
    mock_db = MagicMock()
    user = _make_user(uuid.uuid4())
    user.email_verified = False
    mock_db.scalar.return_value = user

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    client = TestClient(app)

    resp = client.post(
        "/api/v1/auth/verify-email", json={"email": "x@example.com", "code": "123456"}
    )
    assert resp.status_code == 200, resp.text
    assert user.email_verified is True
    app.dependency_overrides.clear()


def test_verify_email_fails_closed_in_production() -> None:
    """With no code store, production must never verify accounts."""
    _clear_rate_buckets()
    mock_db = MagicMock()
    user = _make_user(uuid.uuid4())
    user.email_verified = False
    mock_db.scalar.return_value = user

    app = create_app()
    app.dependency_overrides[get_db] = lambda: mock_db  # type: ignore[assignment]
    client = TestClient(app)

    settings = get_settings()
    with patch.object(settings, "app_env", "production"):
        resp = client.post(
            "/api/v1/auth/verify-email", json={"email": "x@example.com", "code": "123456"}
        )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "not_available"
    assert user.email_verified is False
    app.dependency_overrides.clear()
