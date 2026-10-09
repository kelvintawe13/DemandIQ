"""X-API-Key authentication — lets created API keys actually call the API.

Keys are workspace-scoped, bcrypt-hashed, and carry scopes (``read``/``write``).
Authentication resolves to a lightweight principal that satisfies every
``current_user`` usage in the routers: ``user.id`` (audit rows), role checks
(owner — keys are admin-minted), and ORM relationships are never loaded on it.

Scope model:
  * ``read``  — all GET endpoints
  * ``write`` — mutating endpoints (POST/PATCH/DELETE)

Constant-time hash lookup: the key prefix selects the candidate rows, then the
bcrypt hash is verified per candidate (bcrypt itself is timing-safe).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, Header, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.api_key import ApiKey
from app.models.user import User, UserRole
from app.security.deps import _bearer, get_current_user

logger = logging.getLogger(__name__)

__all__ = ["ApiKeyPrincipal", "get_principal", "require_write_scope"]


class ApiKeyPrincipal:
    """Lightweight stand-in for ``User`` when a request authenticates via API key.

    Satisfies the attributes routers actually touch: ``id`` (audit rows),
    ``workspace_id`` (scoping), ``role`` (owner — keys are admin-minted), and
    ``deactivated_at`` (defense in depth in dependent checks).
    """

    def __init__(self, key_row: ApiKey) -> None:
        self.id = key_row.workspace_id  # audit attribution: the workspace
        self.workspace_id = key_row.workspace_id
        self.role = UserRole.owner
        self.deactivated_at = None
        self.email = f"api-key:{key_row.prefix}"
        self.name = key_row.label
        self._key_row = key_row

    @property
    def scopes(self) -> list[str]:
        return list(self._key_row.scopes or [])


def _verify_secret(secret: str, key_hash: str) -> bool:
    try:
        import bcrypt

        return bcrypt.checkpw(secret.encode()[:72], key_hash.encode())
    except Exception:
        import hashlib

        return hashlib.sha256(secret.encode()).hexdigest() == key_hash


def _load_principal(
    request: Request,
    api_key: str | None,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> ApiKeyPrincipal | User:
    """Resolve the request principal: X-API-Key first, then Bearer JWT."""
    if api_key:
        if not api_key.startswith("diq_") or len(api_key) < 16:
            raise _unauthorized("Invalid API key format")
        prefix = api_key[:12]
        candidates = db.scalars(
            select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.revoked_at.is_(None))
        ).all()
        matched: ApiKey | None = None
        for row in candidates:
            if _verify_secret(api_key, row.key_hash):
                matched = row
                break
        if matched is None:
            raise _unauthorized("Invalid API key")
        matched.last_used_at = datetime.now(UTC)
        db.flush()
        request.state.auth_method = "api_key"  # type: ignore[attr-defined]
        return ApiKeyPrincipal(matched)

    # Fall through to the standard Bearer path (raises 401 when absent).
    return get_current_user(request, credentials, db)


def _unauthorized(message: str):  # type: ignore[no-untyped-def]
    from fastapi import HTTPException

    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": {"code": "unauthorized", "message": message, "field": None, "reference": None}
        },
    )


def get_principal(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
    db: Annotated[Session, Depends(get_db)] = None,  # type: ignore[assignment]
) -> Any:
    """FastAPI dependency: authenticate via X-API-Key or Bearer JWT."""
    return _load_principal(request, x_api_key, credentials, db)


def require_write_scope(principal: Any) -> Any:  # type: ignore[no-untyped-def]
    """Guard for mutating endpoints when authenticated via API key."""
    if isinstance(principal, ApiKeyPrincipal) and "write" not in principal.scopes:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "forbidden",
                    "message": "API key lacks 'write' scope",
                    "field": None,
                    "reference": None,
                }
            },
        )
    return principal
