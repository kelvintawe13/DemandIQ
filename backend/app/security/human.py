"""Human-verification primitives: Cloudflare Turnstile + Google Sign-In.

Both helpers degrade honestly:

* **Turnstile** — when ``settings.turnstile_secret`` is unset (local dev,
  tests, self-hosted without the widget) verification is skipped and the
  reason is logged. When set, the token is checked against Cloudflare's
  ``siteverify`` endpoint; a failed check rejects the request.
* **Google** — ``verify_google_id_token`` validates the JWT signature
  against Google's published JWKS (RS256), plus ``iss``/``aud``/``exp``.
  When ``settings.google_oauth_client_id`` is unset the ``aud`` check is
  relaxed to the token's own ``azp``/``aud`` claim so local sign-in works
  without credentials; production must configure the client id.

No new dependencies: JWKS is fetched with stdlib ``urllib`` and cached
in-process; RS256 verification uses ``python-jose`` (already a dependency).
"""

from __future__ import annotations

import base64
import json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, cast

from fastapi import HTTPException
from jose import jwt as jose_jwt

from app.config import get_settings

logger = logging.getLogger(__name__)

_TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
_GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
_GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

_JWKS_CACHE: dict[str, Any] = {"keys": None, "fetched_at": 0.0}
_JWKS_TTL_SECONDS = 3600.0


def _http_error(
    status_code: int, code: str, message: str, field: str | None = None
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "field": field, "reference": None}},
    )


# ---------------------------------------------------------------------------
# Cloudflare Turnstile
# ---------------------------------------------------------------------------


def verify_turnstile(token: str | None, remote_ip: str | None = None) -> None:
    """Verify a Turnstile token when the server secret is configured.

    No-op (with a log line) when ``turnstile_secret`` is unset or blank.
    Raises HTTP 401 ``captcha_failed`` on any verification failure when set.

    IPv6 note: the browser challenge host (brunhild.challenges.cloudflare.com)
    is IPv6-only; on IPv4-only networks the widget never issues a token. In
    that environment leave TURNSTILE_SECRET empty so this function skips
    verification (dev/CI). The siteverify host
    (challenges.cloudflare.com) has IPv4 and works server-side when reachable.
    """
    cfg = get_settings()
    secret = (cfg.turnstile_secret or "").strip()
    if not secret:
        if token:
            logger.info("Turnstile token presented but no secret configured; skipping verification")
        return
    if not token or not token.strip():
        raise _http_error(
            401, "captcha_required", "Captcha verification required", "turnstile_token"
        )

    body = urllib.parse.urlencode(
        {
            "secret": secret,
            "response": token.strip(),
            **({"remoteip": remote_ip} if remote_ip else {}),
        }
    ).encode()
    try:
        with urllib.request.urlopen(_TURNSTILE_VERIFY_URL, data=body, timeout=10) as resp:
            result = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001 — any failure must fail closed
        logger.warning("Turnstile siteverify unreachable: %s", exc)
        raise _http_error(503, "captcha_unavailable", "Captcha verification unavailable") from None

    if not result.get("success"):
        codes = result.get("error-codes") or []
        logger.info("Turnstile verification failed codes=%s", codes)
        raise _http_error(401, "captcha_failed", "Captcha verification failed", "turnstile_token")


# ---------------------------------------------------------------------------
# Google Sign-In (id_token from Google Identity Services)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GoogleIdentity:
    subject: str
    email: str
    email_verified: bool
    name: str | None
    picture: str | None
    audience: str


def _fetch_google_jwks() -> dict[str, Any]:
    now = time.monotonic()
    if (
        _JWKS_CACHE["keys"] is not None
        and now - float(_JWKS_CACHE["fetched_at"]) < _JWKS_TTL_SECONDS
    ):
        return _JWKS_CACHE["keys"]  # type: ignore[no-any-return]
    try:
        with urllib.request.urlopen(_GOOGLE_JWKS_URL, timeout=10) as resp:
            jwks = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001 — any fetch failure must fail closed
        logger.warning("Google JWKS fetch failed: %s", exc)
        raise _http_error(503, "oauth_unavailable", "Google identity service unavailable") from None
    _JWKS_CACHE["keys"] = jwks
    _JWKS_CACHE["fetched_at"] = now
    return cast(dict[str, Any], jwks)


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _unverified_kid(token: str) -> str | None:
    try:
        header = json.loads(_b64url_decode(token.split(".")[0]).decode())
        kid = header.get("kid")
        return str(kid) if kid else None
    except Exception:  # noqa: BLE001 — malformed header just means "no kid"
        return None


def verify_google_id_token(id_token: str) -> GoogleIdentity:
    """Validate a Google ``id_token`` and return the verified identity.

    Checks: RS256 signature against Google's JWKS, an accepted issuer,
    expiry, and (when configured) the audience. Raises HTTP 401
    ``oauth_failed`` with a specific error code on each failure class.
    """
    cfg = get_settings()
    jwks = _fetch_google_jwks()
    # Key-confusion guard: the token's kid must be one Google actually
    # publishes — never let the verifier fall back to a chosen key.
    kid = _unverified_kid(id_token)
    known_kids = {str(k.get("kid")) for k in jwks.get("keys", []) if k.get("kid")}
    if not kid or kid not in known_kids:
        raise _http_error(401, "oauth_failed", "Unknown signing key")
    try:
        claims: dict[str, Any] = jose_jwt.decode(
            id_token,
            jwks,
            algorithms=["RS256"],
            options={"verify_aud": False, "verify_iss": False},  # checked explicitly below
        )
    except jose_jwt.ExpiredSignatureError:
        raise _http_error(401, "oauth_expired", "Google token expired") from None
    except Exception:  # noqa: BLE001 — any crypto/parse failure is a rejection
        raise _http_error(401, "oauth_failed", "Invalid Google token") from None

    if claims.get("iss") not in _GOOGLE_ISSUERS:
        raise _http_error(401, "oauth_failed", "Untrusted token issuer")

    aud = claims.get("aud")
    azp = claims.get("azp")
    if cfg.google_oauth_client_id:
        audience = str(aud) if isinstance(aud, str) else None
        if audience != cfg.google_oauth_client_id and azp != cfg.google_oauth_client_id:
            raise _http_error(401, "oauth_failed", "Token was not issued for this application")
    elif not aud and not azp:
        # No client id configured and the token carries no audience at all — reject.
        raise _http_error(401, "oauth_failed", "Token missing audience")

    subject = claims.get("sub")
    email = claims.get("email")
    if not subject or not email:
        raise _http_error(401, "oauth_failed", "Google token missing identity claims")

    return GoogleIdentity(
        subject=str(subject),
        email=str(email).lower(),
        email_verified=bool(claims.get("email_verified", False)),
        name=claims.get("name"),
        picture=claims.get("picture"),
        audience=str(aud) if aud else str(azp or ""),
    )
