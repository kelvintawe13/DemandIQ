"""Authentication and authorization dependencies."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import Session as UserSession
from app.models.user import User, UserRole
from app.security.jwt import decode_token

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "field": None, "reference": None}},
    )


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """Resolve the current user from the Bearer token.

    Args:
        request: Incoming request (for logging).
        credentials: HTTP Authorization header.
        db: Database session.

    Returns:
        Authenticated ``User`` instance.

    Raises:
        HTTPException: 401 if the token is missing or invalid.
    """
    if credentials is None or not credentials.credentials:
        raise _error(status.HTTP_401_UNAUTHORIZED, "unauthorized", "Authentication required")

    token = credentials.credentials
    try:
        payload = decode_token(token)
    except Exception:
        raise _error(
            status.HTTP_401_UNAUTHORIZED, "unauthorized", "Invalid or expired token"
        ) from None

    if payload.get("type") != "access":
        raise _error(status.HTTP_401_UNAUTHORIZED, "unauthorized", "Invalid token type")

    sub = payload.get("sub")
    if not sub:
        raise _error(status.HTTP_401_UNAUTHORIZED, "unauthorized", "Invalid token payload")

    try:
        user_id = uuid.UUID(str(sub))
    except ValueError:
        raise _error(status.HTTP_401_UNAUTHORIZED, "unauthorized", "Invalid user id") from None

    user = db.get(User, user_id)
    if user is None or user.deactivated_at is not None:
        raise _error(status.HTTP_401_UNAUTHORIZED, "unauthorized", "User not found or deactivated")

    _require_live_session(db, user, payload)

    # Stash payload on request for middleware / audit
    request.state.jwt_payload = payload  # type: ignore[attr-defined]
    request.state.current_user = user  # type: ignore[attr-defined]
    return user


def _require_live_session(db: Session, user: User, payload: dict[str, Any]) -> None:
    """Refuse a token whose session has been revoked.

    A signed token is a bearer credential with a fixed lifetime, so signing out
    of a device — ``DELETE /me/sessions/{id}``, an administrator revoking a
    seat's sessions, the rotation that follows a refresh — has to be enforced
    here, on every request, or the revoked credential keeps working until it
    expires. ``sid`` names the session the token was issued for (login, MFA
    verify, Google OAuth, refresh); a token without it (an internal token, or
    one minted before the claim existed) has no session to check and is
    accepted on its signature alone.

    A ``sid`` that names a row which is gone, belongs to another user, or has
    been revoked is not a credential — 401, not 403: there is nothing to
    authorize, the caller has to sign in again.
    """
    sid = payload.get("sid")
    if not sid:
        return
    try:
        session_id = uuid.UUID(str(sid))
    except ValueError:
        raise _error(status.HTTP_401_UNAUTHORIZED, "unauthorized", "Invalid session id") from None

    row = db.get(UserSession, session_id)
    if row is None or row.user_id != user.id or row.revoked_at is not None:
        raise _error(
            status.HTTP_401_UNAUTHORIZED,
            "session_revoked",
            "This session is no longer signed in",
        )


def require_role(*allowed: UserRole):  # type: ignore[no-untyped-def]
    """Factory returning a dependency that enforces role membership.

    Usage:
        ``Depends(require_role(UserRole.owner, UserRole.administrator))``
    """

    def _check(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in allowed:
            raise _error(status.HTTP_403_FORBIDDEN, "forbidden", "Insufficient permissions")
        return user

    return _check


def get_current_user_optional(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User | None:
    """Like ``get_current_user`` but returns None instead of 401."""
    if credentials is None or not credentials.credentials:
        return None
    try:
        return get_current_user(request, credentials, db)  # type: ignore[arg-type]
    except HTTPException:
        return None


# Re-export for convenience
__all__ = ["get_current_user", "get_current_user_optional", "get_db", "require_role"]
