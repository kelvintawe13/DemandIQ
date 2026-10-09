"""JWT creation and verification (python-jose)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt

from app.config import get_settings

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def create_access_token(
    *,
    subject: str,
    workspace_id: str,
    role: str,
    expires_minutes: int | None = None,
    session_id: str | None = None,
) -> str:
    """Create a signed JWT access token.

    Args:
        subject: User id (``sub`` claim).
        workspace_id: Workspace id claim.
        role: User role claim.
        expires_minutes: Override for TTL.
        session_id: The ``sessions`` row this token belongs to (``sid`` claim).
            It is what lets an authenticated read say which of the caller's
            sessions is the one making the request.

    Returns:
        Encoded JWT string.
    """
    cfg = get_settings()
    ttl = expires_minutes if expires_minutes is not None else cfg.jwt_ttl_minutes
    now = _now()
    payload: dict[str, Any] = {
        "sub": subject,
        "workspace_id": workspace_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=ttl),
        "jti": str(uuid.uuid4()),
        "type": "access",
    }
    if session_id is not None:
        payload["sid"] = session_id
    return str(jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_algorithm))


def create_refresh_token(
    *,
    subject: str,
    expires_days: int | None = None,
) -> str:
    """Create a signed refresh token.

    Args:
        subject: User id.

    Returns:
        Encoded JWT string with ``type=refresh``.
    """
    cfg = get_settings()
    ttl = expires_days if expires_days is not None else cfg.refresh_ttl_days
    now = _now()
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(days=ttl),
        "jti": str(uuid.uuid4()),
        "type": "refresh",
    }
    return str(jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_algorithm))


def decode_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT.

    Args:
        token: Encoded JWT.

    Returns:
        Decoded payload.

    Raises:
        JWTError: If the token is invalid or expired.
    """
    cfg = get_settings()
    try:
        payload = jwt.decode(token, cfg.jwt_secret, algorithms=[cfg.jwt_algorithm])
        return payload  # type: ignore[no-any-return]
    except JWTError as exc:
        logger.debug("JWT decode failed: %s", exc)
        raise


def decode_token_unsafe(token: str) -> dict[str, Any] | None:
    """Decode without raising — returns None on failure (for middleware)."""
    try:
        return decode_token(token)
    except JWTError:
        return None
