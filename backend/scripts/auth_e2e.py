#!/usr/bin/env python3
"""Automated auth E2E — Google OAuth, Turnstile, silent refresh, profile gate.

Phases (env PHASE, default 1):
  1  dev-safe settings: full Google flow against a local JWKS (Google wire
     format), silent refresh, logout revocation, /me/identities.
  2  TURNSTILE_SECRET set: captcha_required + real Cloudflare verdict on a
     bogus token.
  3  GOOGLE_OAUTH_CLIENT_ID set: audience enforcement, fresh sign-up role +
     profile gate, and the /me/profile/complete lifecycle.

Usage:  PHASE=1 DATABASE_URL=... python scripts/auth_e2e.py
        (orchestrated by scripts/auth_e2e.sh)
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk as jose_jwk
from jose import jwt as jose_jwt

# The address of the server this harness starts. The scheme is deliberately
# part of the literal that builds every request URL (see `call`) rather than a
# variable, so no request can be redirected to `file:` or another scheme.
HOST = "127.0.0.1:" + os.environ.get("PORT", "8011")
PHASE = os.environ.get("PHASE", "1")
PASSES: list[str] = []


def ok(name, detail=""):
    PASSES.append(name)
    print(f"PASS {name}  {detail}")


def call(method, path, body=None, token=None):
    req = urllib.request.Request(
        "http://" + HOST + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        # The Request just built carries the literal `http://` prefix above, so
        # the scheme cannot be `file:` or a custom one.
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        return e.code, (json.loads(raw) if raw else None)


def wait_healthy():
    for _ in range(60):
        code, _ = call("GET", "/health")
        if code == 200:
            return
        time.sleep(0.5)
    raise SystemExit("server never became healthy")


# --- local JWKS server (Google wire format) ---------------------------------
KEY_ID = "e2e-key-1"
priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
pub_jwk = jose_jwk.RSAKey(algorithm="RS256", key=priv.public_key()).to_dict()
pub_jwk.update({"kid": KEY_ID, "alg": "RS256", "use": "sig"})


class JWKSHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"keys": [pub_jwk]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", 8912), JWKSHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()

wait_healthy()
now = int(time.time())


def mint(extra=None, kid=KEY_ID, aud="e2e-client-id"):
    claims = {
        "iss": "https://accounts.google.com",
        "aud": aud,
        "azp": aud,
        "sub": "e2e-google-sub-1",
        "email": "aline.e2e@gmail.com",
        "email_verified": True,
        "name": "Aline Umutoni",
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(extra or {})
    return jose_jwt.encode(claims, priv, algorithm="RS256", headers={"kid": kid})


if PHASE == "1":
    code, d = call("POST", "/api/v1/auth/oauth/google", {"id_token": mint()})
    assert code == 200, d
    u1 = d["user"]["id"]
    assert d["access_token"] and d["refresh_token"]
    ok("google-signup-provisions", f"user={d['user']['email']} role={d['user']['role']}")
    google_access = d["access_token"]

    code, d = call("GET", "/api/v1/auth/me", token=google_access)
    assert code == 200 and d["user"]["email"] == "aline.e2e@gmail.com", d
    ok("auth-me-with-google-token", "200")

    code, d = call("POST", "/api/v1/auth/oauth/google", {"id_token": mint()})
    assert code == 200 and d["user"]["id"] == u1, d
    ok("google-repeat-links-same-user", "same user id")

    head, pay, sig = mint().split(".")
    bad_sig = ("A" if sig[0] != "A" else "B") + sig[1:]
    code, d = call("POST", "/api/v1/auth/oauth/google", {"id_token": f"{head}.{pay}.{bad_sig}"})
    assert code == 401 and d["error"]["code"] == "oauth_failed", (code, d)
    ok("google-tampered-signature-rejected", "401 oauth_failed")

    code, d = call(
        "POST", "/api/v1/auth/oauth/google", {"id_token": mint({"iss": "https://evil.example"})}
    )
    assert code == 401, (code, d)
    ok("google-bad-issuer-rejected", "401")

    code, d = call("POST", "/api/v1/auth/oauth/google", {"id_token": mint({"exp": now - 10})})
    assert code == 401 and d["error"]["code"] == "oauth_expired", (code, d)
    ok("google-expired-rejected", "401 oauth_expired")

    code, d = call("POST", "/api/v1/auth/oauth/google", {"id_token": mint(kid="rogue-key")})
    assert code == 401, (code, d)
    ok("google-unknown-kid-rejected", "401")

    code, d = call(
        "POST",
        "/api/v1/auth/register",
        {
            "name": "Pw User",
            "email": "pw.e2e@example.com",
            "password": "password123",
            "business_name": "Pw Bistro",
            "turnstile_token": None,
        },
    )
    assert code == 201, d
    ok("register-turnstile-skip", "201")

    code, d = call(
        "POST", "/api/v1/auth/login", {"email": "pw.e2e@example.com", "password": "password123"}
    )
    assert code == 200 and d["access_token"] and d["refresh_token"], d
    pw_access, pw_refresh = d["access_token"], d["refresh_token"]
    ok("login-turnstile-skip", "200 tokens issued")

    code, d = call("POST", "/api/v1/auth/refresh", {"refresh_token": pw_refresh})
    assert code == 200 and d["access_token"] != pw_access, d
    new_access = d["access_token"]
    rotated = d.get("refresh_token")
    assert rotated and rotated != pw_refresh, "rotation must return a fresh refresh token"
    code, d = call("GET", "/api/v1/auth/me", token=new_access)
    assert code == 200, d
    ok("silent-refresh-contract", "new access token authorized, refresh rotated")

    # Rotation: the presented token is single-use — replay must fail.
    code, d = call("POST", "/api/v1/auth/refresh", {"refresh_token": pw_refresh})
    assert code == 401, (code, d)
    ok("refresh-rotation-replay-rejected", "401 on reused token")

    # Rotation revokes the session the old token named, so the ACCESS token from
    # before the refresh must stop working too. Otherwise "rotation" only killed
    # the refresh chain while the old access token stayed valid until it expired.
    code, d = call("GET", "/api/v1/auth/me", token=pw_access)
    assert code == 401, (code, d)
    ok("rotation-invalidates-previous-access-token", "401 on the pre-rotation access token")

    code, d = call("POST", "/api/v1/auth/refresh", {"refresh_token": rotated})
    assert code == 200 and d.get("refresh_token") not in (None, rotated), d
    pw_refresh = d["refresh_token"]
    ok("refresh-rotation-chain", "rotated token refreshes again")

    code, d = call("POST", "/api/v1/auth/refresh", {"refresh_token": "not-a-jwt"})
    assert code == 401, (code, d)
    ok("refresh-garbage-rejected", "401")

    code, _ = call("POST", "/api/v1/auth/logout", {"refresh_token": pw_refresh})
    assert code == 204, code
    code, d = call("POST", "/api/v1/auth/refresh", {"refresh_token": pw_refresh})
    assert code == 401, (code, d)
    ok("logout-revokes-refresh", "401 after revoke")

    # Signing a device out must end that device's access token immediately.
    # Two logins create two sessions; revoke the second with the first and prove
    # the second can no longer read anything — the E2E that would have caught the
    # bearer-token staleness, since a unit test with a mocked DB cannot see it.
    code, d = call(
        "POST", "/api/v1/auth/login", {"email": "pw.e2e@example.com", "password": "password123"}
    )
    assert code == 200 and d["access_token"], d
    device_a = d["access_token"]
    code, d = call(
        "POST", "/api/v1/auth/login", {"email": "pw.e2e@example.com", "password": "password123"}
    )
    assert code == 200 and d["access_token"], d
    device_b = d["access_token"]

    code, d = call("GET", "/api/v1/me/sessions", token=device_a)
    assert code == 200, (code, d)
    rows = d["data"]
    current = [r for r in rows if r.get("current")]
    others = [r for r in rows if not r.get("current")]
    assert len(current) == 1 and others, (current, others)
    ok("sessions-list-marks-exactly-one-current", f"{len(rows)} rows, 1 current")

    # The API is honest about what the origin field holds: an address, not a
    # country code, in this deployment.
    assert "origin" in rows[0] and "ip_country" not in rows[0], rows[0]
    assert rows[0]["origin"], rows[0]
    ok("session-origin-is-named-for-what-it-holds", f"origin={rows[0]['origin']}")

    victim = others[0]["id"]
    code, _ = call("DELETE", f"/api/v1/me/sessions/{victim}", token=device_a)
    assert code == 204, code
    code, d = call("GET", "/api/v1/auth/me", token=device_b)
    assert code == 401, (code, d)
    assert d["error"]["code"] == "session_revoked", d
    ok("revoked-session-token-refused", "401 session_revoked on the signed-out device")

    # …while the device that did the revoking keeps working.
    code, _ = call("GET", "/api/v1/auth/me", token=device_a)
    assert code == 200, code
    ok("other-device-still-authorized", "the calling session is unaffected")

    code, d = call("GET", "/api/v1/me/identities", token=google_access)
    assert code == 200, (code, d)
    items = d if isinstance(d, list) else d.get("data", [])
    assert any((i.get("provider") == "google") for i in items), d
    ok("me-identities-lists-google", "google linked")

    print(f"PHASE1-OK {len(PASSES)} checks")

if PHASE == "2":
    code, d = call(
        "POST", "/api/v1/auth/login", {"email": "pw.e2e@example.com", "password": "password123"}
    )
    assert code == 401 and d["error"]["code"] == "captcha_required", (code, d)
    ok("turnstile-required-when-enforced", "401 captcha_required")

    code, d = call(
        "POST",
        "/api/v1/auth/login",
        {
            "email": "pw.e2e@example.com",
            "password": "password123",
            "turnstile_token": "dummy-token",
        },
    )
    assert code in (401, 503), (code, d)
    ok("turnstile-fails-closed", f"{code} {d['error']['code']}")

    print(f"PHASE2-OK {len(PASSES)} checks")

if PHASE == "3":
    cid = os.environ["GOOGLE_OAUTH_CLIENT_ID"]

    code, d = call(
        "POST", "/api/v1/auth/oauth/google", {"id_token": mint(aud="other-app-client-id")}
    )
    assert code == 401 and d["error"]["code"] == "oauth_failed", (code, d)
    ok("google-audience-enforcement", "foreign client_id rejected")

    code, d = call("POST", "/api/v1/auth/oauth/google", {"id_token": mint(aud=cid)})
    assert code == 200 and d["access_token"] and d["refresh_token"], (code, d)
    assert d["user"]["role"] == "viewer", d["user"]["role"]
    assert d["needs_profile"] is True
    ok(
        "google-signup-role-viewer-and-gated",
        f"user={d['user']['email']} role=viewer needs_profile=true",
    )
    access, refresh = d["access_token"], d["refresh_token"]

    # Gate lifecycle: complete once -> 200 + flag cleared; repeat -> 409.
    code, d = call(
        "POST",
        "/api/v1/me/profile/complete",
        {"name": "Aline Umutoni", "job_title": "Operations manager"},
        token=access,
    )
    assert code == 200 and d["profile_completed"] is True, (code, d)
    assert d["role"] == "viewer", d["role"]
    ok("profile-complete-sets-flag", "role stays viewer")

    code, d = call("POST", "/api/v1/me/profile/complete", {"name": "Aline Again"}, token=access)
    assert code == 409 and d["error"]["code"] == "profile_completed", (code, d)
    ok("profile-complete-idempotent-guard", "409 profile_completed")

    code, d = call("POST", "/api/v1/auth/refresh", {"refresh_token": refresh})
    assert code == 200, (code, d)
    ok("silent-refresh-contract", "new access token issued")

    print(f"PHASE3-OK {len(PASSES)} checks")
