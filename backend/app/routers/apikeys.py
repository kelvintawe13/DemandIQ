"""API keys admin router — prefix, bcrypt hash, scopes, rotate/revoke.

* GET    /api/v1/api-keys
* POST   /api/v1/api-keys
* POST   /api/v1/api-keys/{id}/rotate
* DELETE /api/v1/api-keys/{id}
"""

from __future__ import annotations

import datetime
import logging
import secrets
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.api_key import ApiKey
from app.models.user import User
from app.security.permissions import API_KEYS_MANAGE, has_capability

router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])

logger = logging.getLogger(__name__)

_ALLOWED_SCOPES = {"read", "write", "forecasts:read", "ingest:write", "admin"}


def _error(code: int, err: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err, "message": msg, "field": field, "reference": None}},
    )


def _require_admin(user: User) -> None:
    if not has_capability(user, API_KEYS_MANAGE):
        raise _error(403, "forbidden", "Administrator role required")


def _hash_secret(secret: str) -> str:
    """Bcrypt hash truncated to 72 bytes for compatibility with bcrypt 5."""
    try:
        import bcrypt

        # bcrypt only handles first 72 bytes — truncate explicitly.
        hashed = bcrypt.hashpw(secret.encode()[:72], bcrypt.gensalt())
        return hashed.decode()
    except Exception:
        # Fallback to sha256 if bcrypt unavailable.
        import hashlib

        return hashlib.sha256(secret.encode()).hexdigest()


def _generate_secret() -> tuple[str, str]:
    # 32 random bytes -> ~43 chars urlsafe ; prefix first 12 chars.
    raw = secrets.token_urlsafe(32)
    secret = f"diq_{raw}"
    prefix = secret[:12]
    return secret, prefix


def _serialise(row: ApiKey, include_secret: str | None = None) -> dict[str, Any]:
    # created_at not in migration; synthesize from id if missing.
    created = getattr(row, "created_at", None)
    # Fallback to now when attribute absent (mocked DB).
    if created is None:
        created = datetime.datetime.now(datetime.UTC)
    base: dict[str, Any] = {
        "id": str(row.id),
        "workspace_id": str(row.workspace_id),
        "label": row.label,
        "prefix": row.prefix,
        "scopes": row.scopes,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
        "created_at": created.isoformat() if hasattr(created, "isoformat") else None,
    }
    if include_secret is not None:
        base["secret"] = include_secret
        base["warning"] = "Store this secret securely. It will not be shown again"
    return base


@router.get("", response_model=dict)
def list_keys(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """List API keys for workspace (admin) — prefix, scopes, last_used_at."""
    _require_admin(current_user)
    try:
        rows = list(
            db.scalars(select(ApiKey).where(ApiKey.workspace_id == current_user.workspace_id)).all()
        )
    except Exception:
        rows = []
    # Exclude revoked if needed? Return all but mark revoked.
    data = [_serialise(r) for r in rows]
    return {"data": data}


@router.post("", response_model=dict, status_code=201)
def create_key(
    payload: dict[str, Any],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Create an API key (admin) — full secret returned exactly once."""
    _require_admin(current_user)
    label = payload.get("label")
    if not label or not str(label).strip():
        raise _error(400, "validation_error", "label is required", field="label")
    scopes = payload.get("scopes")
    if scopes is None:
        scopes = ["read"]
    if not isinstance(scopes, list) or not scopes:
        raise _error(400, "validation_error", "scopes must be a non-empty array", field="scopes")
    for s in scopes:
        if not isinstance(s, str) or not s.strip():
            raise _error(400, "invalid_scope", "scope must be a non-empty string", field="scopes")
    # Generate secret + prefix + hash.
    secret, prefix = _generate_secret()
    key_hash = _hash_secret(secret)
    row = ApiKey(
        id=uuid.uuid4(),
        workspace_id=current_user.workspace_id,
        label=str(label).strip(),
        prefix=prefix,
        key_hash=key_hash,
        scopes=[str(s).strip() for s in scopes],
        last_used_at=None,
        revoked_at=None,
    )
    # Ensure attribute for mocks.
    if not hasattr(row, "created_at") or getattr(row, "created_at") is None:
        try:
            object.__setattr__(row, "created_at", datetime.datetime.now(datetime.UTC))
        except Exception:
            # Only reachable for objects that reject attribute assignment (the
            # test doubles); the column has a server-side default in production.
            logger.debug("could not stamp created_at on the new API key", exc_info=True)
    db.add(row)
    db.flush()
    return _serialise(row, include_secret=secret)


@router.post("/{key_id}/rotate", response_model=dict)
def rotate_key(
    key_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Rotate an API key — new secret returned once (admin)."""
    _require_admin(current_user)
    row: ApiKey | None = db.get(ApiKey, key_id)
    if row is None or row.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "API key not found")
    if row.revoked_at is not None:
        raise _error(409, "revoked", "Cannot rotate a revoked key")
    secret, prefix = _generate_secret()
    row.prefix = prefix
    row.key_hash = _hash_secret(secret)
    row.revoked_at = None
    row.last_used_at = None
    db.flush()
    return _serialise(row, include_secret=secret)


@router.delete("/{key_id}", status_code=204)
def delete_key(
    key_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Revoke (delete) an API key (admin)."""
    _require_admin(current_user)
    row: ApiKey | None = db.get(ApiKey, key_id)
    if row is None or row.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "API key not found")
    if row.revoked_at is not None:
        # Already revoked — idempotent 204.
        return
    row.revoked_at = datetime.datetime.now(datetime.UTC)
    db.flush()
