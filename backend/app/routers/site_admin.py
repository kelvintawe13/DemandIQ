"""Site administration API — manage the site, not just a workspace.

Surface (all gated to the ``is_site_admin`` flag, every write audited):
  GET    /api/v1/admin/site/overview          platform stats (users, workspaces, forecasts, models)
  GET    /api/v1/admin/site/health            runtime health: db latency, uptime, revision, live sessions
  GET    /api/v1/admin/site/users             cross-workspace user directory (search/role/status filter)
  GET    /api/v1/admin/site/users/{id}        one account in full: workspace, sessions, MFA, audit trail
  PATCH  /api/v1/admin/site/users/{id}        change role / deactivate / reactivate
  POST   /api/v1/admin/site/users/{id}/reset-password   set a new password directly
  GET    /api/v1/admin/site/workspaces        all workspaces with counts
  GET    /api/v1/admin/site/workspaces/{id}   one workspace: footprint, members, forecast coverage
  PATCH  /api/v1/admin/site/workspaces/{id}   rename / change locale metadata
  GET    /api/v1/admin/site/settings          platform settings (defaults merged)
  PATCH  /api/v1/admin/site/settings          upsert settings
  GET    /api/v1/admin/site/audit             privileged actions (search/action/client filter)

Directory writes enforce the same hierarchy as member management
(``app/security/permissions.py``): you may only act on accounts below your own
rank, may only grant roles strictly below it, and never ownership. Site
administrators are protected from demotion/deactivation here — the flag is
seeded and transferred deliberately, not by directory edit.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.audit import AuditLog
from app.models.forecast import Forecast, ModelVersion
from app.models.location import Location
from app.models.user import Session as UserSession
from app.models.user import User, UserIdentity, UserRole
from app.models.workspace import Workspace
from app.security.deps import get_current_user
from app.security.hashing import hash_password
from app.security.permissions import ROLE_LABELS, can_assign_role, can_manage_member

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin/site", tags=["site-admin"])

# Process start, for the uptime readout on the health panel.
_STARTED_AT = time.monotonic()


def _error(code_int: int, code: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code_int,
        detail={"error": {"code": code, "message": msg, "field": field, "reference": None}},
    )


def _require_admin(user: User) -> None:
    """Site admins are flagged on the account, not derived from workspace role.

    A workspace ``owner`` manages their own workspace's data; managing the
    *platform* (all users, workspaces, settings) requires the explicit
    ``is_site_admin`` flag held by the seeded admin account.
    """
    if not bool(getattr(user, "is_site_admin", False)):
        raise _error(403, "forbidden", "Site administrator role required")


def _guard_role_change(actor: User, target: User, new_role: UserRole) -> None:
    """Refuse a directory role change the hierarchy does not permit.

    Applies the *same* rules as member management (``permissions.py``) so the
    site directory cannot do what Access Control refuses. Most importantly,
    ownership can never be granted here, and no one promotes themselves.
    """
    if target.id == actor.id:
        raise _error(422, "validation_error", "You cannot change your own role", field="role")
    if not can_manage_member(actor, target):
        label = ROLE_LABELS.get(target.role, "That account")
        raise _error(403, "forbidden", f"{label} is at or above your role", field="role")
    if not can_assign_role(actor, new_role):
        label = ROLE_LABELS.get(new_role, new_role.value)
        raise _error(403, "forbidden", f"You cannot grant {label} from your role", field="role")
    if bool(getattr(target, "is_site_admin", False)) and target.role != new_role:
        raise _error(
            403,
            "forbidden",
            "Site administrators are not re-roled from the directory",
            field="role",
        )


def _guard_deactivation(actor: User, target: User) -> None:
    """Refuse deactivating yourself, a peer-or-above, or a site administrator."""
    if target.id == actor.id:
        raise _error(
            422, "validation_error", "You cannot deactivate your own account", field="deactivated"
        )
    if not can_manage_member(actor, target):
        label = ROLE_LABELS.get(target.role, "That account")
        raise _error(403, "forbidden", f"{label} is at or above your role", field="deactivated")
    if bool(getattr(target, "is_site_admin", False)):
        raise _error(
            403,
            "forbidden",
            "Site administrators are not deactivated from the directory",
            field="deactivated",
        )


def _client(request: Request) -> str:
    ua = (request.headers.get("user-agent") or "").lower()
    if "mobile" in ua:
        return "mobile"
    return "web"


def _audit(
    db: Session,
    actor: User,
    action: str,
    target: str | None,
    request: Request,
    meta: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            workspace_id=actor.workspace_id,
            actor_id=actor.id,
            action=action,
            target=target,
            client=_client(request),
            meta_data=meta or {},
            created_at=datetime.now(UTC),
        )
    )
    db.flush()


# --------------------------------------------------------------------- stats


@router.get("/overview")
def site_overview(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_admin(current_user)
    try:
        users_total = db.scalar(select(func.count()).select_from(User)) or 0
        users_active = (
            db.scalar(select(func.count()).select_from(User).where(User.deactivated_at.is_(None)))
            or 0
        )
        workspaces_total = db.scalar(select(func.count()).select_from(Workspace)) or 0
        locations_total = db.scalar(select(func.count()).select_from(Location)) or 0
        forecasts_total = db.scalar(select(func.count()).select_from(Forecast)) or 0
        models_total = db.scalar(select(func.count()).select_from(ModelVersion)) or 0
        roles_rows = db.execute(text("SELECT role, count(*) FROM users GROUP BY role")).all()
        recent_users = db.scalars(select(User).order_by(User.created_at.desc()).limit(5)).all()
        return {
            "users": {"total": int(users_total), "active": int(users_active)},
            "workspaces": int(workspaces_total),
            "locations": int(locations_total),
            "forecasts": int(forecasts_total),
            "models": int(models_total),
            "roles": {str(r[0]): int(r[1]) for r in roles_rows},
            "recent_users": [
                {
                    "id": str(u.id),
                    "email": str(u.email),
                    "name": u.name,
                    "role": u.role.value,
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                }
                for u in recent_users
            ],
        }
    except Exception:
        logger.exception("site overview failed")
        raise _error(500, "internal", "Overview failed") from None


# -------------------------------------------------------------------- health


def _optional_scalar(db: Session, stmt: Any) -> Any:
    """A scalar read that degrades to ``None`` instead of failing the request.

    The health panel reports on the platform, so a missing migration table or
    an empty pipeline must read as "unknown" rather than a 500.
    """
    try:
        return db.scalar(stmt)
    except Exception:  # health must not raise
        logger.warning("site health probe failed", exc_info=True)
        return None


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


@router.get("/health")
def site_health(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Runtime facts about the platform, for the console's health panel."""
    _require_admin(current_user)
    cfg = get_settings()
    checked = time.perf_counter()
    database = "ok"
    latency_ms: float | None = None
    try:
        db.execute(text("SELECT 1"))
        latency_ms = round((time.perf_counter() - checked) * 1000, 1)
    except Exception:  # a dead DB is the thing we are reporting
        logger.warning("site health: database check failed", exc_info=True)
        database = "unavailable"

    revision = _optional_scalar(db, text("SELECT version_num FROM alembic_version"))
    active_sessions = _optional_scalar(
        db,
        select(func.count()).select_from(UserSession).where(UserSession.revoked_at.is_(None)),
    )
    return {
        "app": {
            "name": cfg.app_name,
            "version": cfg.app_version,
            "environment": cfg.app_env,
            "debug": cfg.debug,
        },
        "database": {"status": database, "latency_ms": latency_ms},
        "uptime_seconds": int(time.monotonic() - _STARTED_AT),
        "migration_revision": str(revision) if revision is not None else None,
        "sessions": {"active": int(active_sessions or 0)},
        "pipeline": {
            "latest_forecast_at": _iso(_optional_scalar(db, select(func.max(Forecast.created_at)))),
            "latest_training_at": _iso(
                _optional_scalar(db, select(func.max(ModelVersion.trained_at)))
            ),
        },
        "settings": {
            "maintenance_mode": bool(get_platform_setting(db, "maintenance_mode")),
            "registration_open": bool(get_platform_setting(db, "registration_open")),
        },
    }


# --------------------------------------------------------------------- users


@router.get("/users")
def list_users(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    q: str | None = Query(default=None),
    role: str | None = Query(default=None),
    status: str | None = Query(default=None, pattern="^(active|deactivated|unverified)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    _require_admin(current_user)
    stmt = select(User).order_by(User.created_at.desc())
    count_stmt = select(func.count()).select_from(User)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(func.lower(User.email).like(like) | func.lower(User.name).like(like))
        count_stmt = count_stmt.where(
            func.lower(User.email).like(like) | func.lower(User.name).like(like)
        )
    if role:
        try:
            role_enum = UserRole(role)
        except ValueError:
            raise _error(422, "validation_error", f"Unknown role '{role}'", field="role") from None
        stmt = stmt.where(User.role == role_enum)
        count_stmt = count_stmt.where(User.role == role_enum)
    if status == "deactivated":
        stmt = stmt.where(User.deactivated_at.is_not(None))
        count_stmt = count_stmt.where(User.deactivated_at.is_not(None))
    elif status == "active":
        stmt = stmt.where(User.deactivated_at.is_(None), User.email_verified.is_(True))
        count_stmt = count_stmt.where(User.deactivated_at.is_(None), User.email_verified.is_(True))
    elif status == "unverified":
        stmt = stmt.where(User.deactivated_at.is_(None), User.email_verified.is_(False))
        count_stmt = count_stmt.where(User.deactivated_at.is_(None), User.email_verified.is_(False))
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(stmt.limit(limit).offset(offset)).all()
    ws_names: dict[str, str] = {}
    if rows:
        ws_ids = {u.workspace_id for u in rows}
        ws_rows = db.execute(
            text("SELECT id::text, name FROM workspaces WHERE id::text = ANY(:ids)"),
            {"ids": [str(w) for w in ws_ids]},
        ).all()
        ws_names = {str(r[0]): str(r[1]) for r in ws_rows}
    return {
        "data": [
            {
                "id": str(u.id),
                "email": str(u.email),
                "name": u.name,
                "role": u.role.value,
                "workspace_id": str(u.workspace_id),
                "workspace_name": ws_names.get(str(u.workspace_id)),
                "email_verified": u.email_verified,
                "mfa_enabled": u.mfa_enabled,
                "profile_completed": u.profile_completed,
                "is_site_admin": bool(getattr(u, "is_site_admin", False)),
                "deactivated_at": u.deactivated_at.isoformat() if u.deactivated_at else None,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in rows
        ],
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < int(total),
    }


@router.get("/users/{user_id}")
def get_user_detail(
    user_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """One account in full — the drill-down the directory rows open into.

    Returns the account, its workspace, live sessions (device / country /
    last-seen), linked identities, and that account's own recent audit trail.
    """
    _require_admin(current_user)
    user = db.get(User, user_id)
    if user is None:
        raise _error(404, "not_found", "User not found")
    ws = db.get(Workspace, user.workspace_id)
    sessions = db.scalars(
        select(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .order_by(UserSession.last_seen_at.desc())
        .limit(20)
    ).all()
    identities = db.scalars(
        select(UserIdentity).where(UserIdentity.user_id == user.id).limit(10)
    ).all()
    events = db.scalars(
        select(AuditLog)
        .where(AuditLog.actor_id == user.id)
        .order_by(AuditLog.created_at.desc())
        .limit(10)
    ).all()
    last_seen = sessions[0].last_seen_at if sessions else None
    return {
        "id": str(user.id),
        "name": user.name,
        "email": str(user.email),
        "role": user.role.value,
        "role_label": ROLE_LABELS.get(user.role, "Viewer"),
        "job_title": user.job_title,
        "phone": user.phone,
        "mfa_enabled": user.mfa_enabled,
        "email_verified": user.email_verified,
        "profile_completed": user.profile_completed,
        "is_site_admin": bool(getattr(user, "is_site_admin", False)),
        "deactivated_at": user.deactivated_at.isoformat() if user.deactivated_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_seen_at": last_seen.isoformat() if last_seen else None,
        "can_manage": user.id != current_user.id and can_manage_member(current_user, user),
        "workspace": (
            {"id": str(ws.id), "name": ws.name, "business_type": ws.business_type}
            if ws is not None
            else None
        ),
        "identities": [{"provider": i.provider} for i in identities],
        "sessions": [
            {
                "id": str(s.id),
                "device": s.device,
                "origin": s.origin,
                "trusted_device": s.trusted_device,
                "last_seen_at": s.last_seen_at.isoformat() if s.last_seen_at else None,
            }
            for s in sessions
        ],
        "recent_events": [
            {
                "id": e.id,
                "action": e.action,
                "target": e.target,
                "client": e.client,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in events
        ],
    }


class UserPatch(BaseModel):
    role: str | None = None
    deactivated: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)


@router.patch("/users/{user_id}")
def patch_user(
    user_id: uuid.UUID,
    payload: UserPatch,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_admin(current_user)
    user = db.get(User, user_id)
    if user is None:
        raise _error(404, "not_found", "User not found")
    before: dict[str, Any] = {}
    changes: dict[str, Any] = {}

    if payload.role is not None:
        try:
            new_role = UserRole(payload.role)
        except ValueError:
            raise _error(
                422, "validation_error", f"Unknown role '{payload.role}'", field="role"
            ) from None
        _guard_role_change(current_user, user, new_role)
        before["role"] = user.role.value
        user.role = new_role
        changes["role"] = new_role.value

    if payload.deactivated is not None:
        if payload.deactivated:
            _guard_deactivation(current_user, user)
        else:
            # Reactivating restores access, so it needs the same rank as
            # deactivating — you cannot restore an account you cannot manage.
            if user.id == current_user.id:
                raise _error(
                    422,
                    "validation_error",
                    "You cannot change your own account status",
                    field="deactivated",
                )
            if not can_manage_member(current_user, user):
                label = ROLE_LABELS.get(user.role, "That account")
                raise _error(
                    403, "forbidden", f"{label} is at or above your role", field="deactivated"
                )
        before["deactivated_at"] = user.deactivated_at.isoformat() if user.deactivated_at else None
        user.deactivated_at = datetime.now(UTC) if payload.deactivated else None
        changes["deactivated"] = payload.deactivated

    if payload.name is not None:
        if user.id != current_user.id and not can_manage_member(current_user, user):
            raise _error(
                403, "forbidden", "You may only rename accounts below your role", field="name"
            )
        before["name"] = user.name
        user.name = payload.name
        changes["name"] = payload.name

    if not changes:
        raise _error(422, "validation_error", "No changes supplied")

    db.flush()
    _audit(
        db,
        current_user,
        "site.user.update",
        str(user.id),
        request,
        {"before": before, "after": changes},
    )
    return {
        "id": str(user.id),
        "role": user.role.value,
        "deactivated_at": user.deactivated_at.isoformat() if user.deactivated_at else None,
        "name": user.name,
    }


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


@router.post("/users/{user_id}/reset-password")
def admin_reset_password(
    user_id: uuid.UUID,
    payload: PasswordReset,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_admin(current_user)
    user = db.get(User, user_id)
    if user is None:
        raise _error(404, "not_found", "User not found")
    user.password_hash = hash_password(payload.new_password)
    db.flush()
    # Revoking live sessions forces re-login with the new credential.
    db.execute(
        text("UPDATE sessions SET revoked_at=COALESCE(revoked_at, now()) WHERE user_id=:uid"),
        {"uid": str(user.id)},
    )
    db.flush()
    _audit(db, current_user, "site.user.password_reset", str(user.id), request)
    return {"id": str(user.id), "password_reset": True}


# --------------------------------------------------------------- workspaces


@router.get("/workspaces")
def list_workspaces(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_admin(current_user)
    rows = db.execute(
        text(
            "SELECT w.id, w.name, w.business_type, w.country, w.timezone, w.created_at, "
            "(SELECT count(*) FROM users u WHERE u.workspace_id = w.id) AS users, "
            "(SELECT count(*) FROM locations l WHERE l.workspace_id = w.id) AS locations, "
            "(SELECT count(*) FROM forecasts f WHERE f.workspace_id = w.id) AS forecasts "
            "FROM workspaces w ORDER BY w.created_at DESC"
        )
    ).all()
    return {
        "data": [
            {
                "id": str(r[0]),
                "name": r[1],
                "business_type": r[2],
                "country": r[3],
                "timezone": r[4],
                "created_at": r[5].isoformat() if r[5] else None,
                "users": int(r[6]),
                "locations": int(r[7]),
                "forecasts": int(r[8]),
            }
            for r in rows
        ]
    }


class WorkspacePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    business_type: str | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    timezone: str | None = None


@router.get("/workspaces/{workspace_id}")
def get_workspace_detail(
    workspace_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """One workspace's footprint — what the row's drill-down opens into."""
    _require_admin(current_user)
    ws = db.get(Workspace, workspace_id)
    if ws is None:
        raise _error(404, "not_found", "Workspace not found")

    def count(model: Any) -> int:
        return int(
            _optional_scalar(db, select(func.count()).select_from(model).where(model.workspace_id == ws.id))  # type: ignore[attr-defined]
            or 0
        )

    models_registered = int(
        _optional_scalar(
            db,
            select(func.count())
            .select_from(ModelVersion)
            .where(ModelVersion.workspace_id == ws.id),
        )
        or 0
    )
    members = db.scalars(
        select(User).where(User.workspace_id == ws.id).order_by(User.created_at.desc()).limit(10)
    ).all()
    return {
        "id": str(ws.id),
        "name": ws.name,
        "business_type": ws.business_type,
        "country": ws.country,
        "timezone": ws.timezone,
        "created_at": ws.created_at.isoformat() if ws.created_at else None,
        "counts": {
            "users": count(User),
            "locations": count(Location),
            "forecasts": count(Forecast),
            "models": models_registered,
        },
        "forecast_window": {
            "first": str(
                _optional_scalar(
                    db, select(func.min(Forecast.target_date)).where(Forecast.workspace_id == ws.id)
                )
                or ""
            )
            or None,
            "last": str(
                _optional_scalar(
                    db, select(func.max(Forecast.target_date)).where(Forecast.workspace_id == ws.id)
                )
                or ""
            )
            or None,
        },
        "members": [
            {"id": str(m.id), "name": m.name, "email": str(m.email), "role": m.role.value}
            for m in members
        ],
    }


@router.patch("/workspaces/{workspace_id}")
def patch_workspace(
    workspace_id: uuid.UUID,
    payload: WorkspacePatch,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_admin(current_user)
    ws = db.get(Workspace, workspace_id)
    if ws is None:
        raise _error(404, "not_found", "Workspace not found")
    before: dict[str, Any] = {}
    changes: dict[str, Any] = {}
    for attr in ("name", "business_type", "country", "timezone"):
        value = getattr(payload, attr)
        if value is not None:
            before[attr] = getattr(ws, attr)
            setattr(ws, attr, value)
            changes[attr] = value
    if not changes:
        raise _error(422, "validation_error", "No changes supplied")
    db.flush()
    _audit(
        db,
        current_user,
        "site.workspace.update",
        str(ws.id),
        request,
        {"before": before, "after": changes},
    )
    return {"id": str(ws.id), "name": ws.name, "timezone": ws.timezone}


# ----------------------------------------------------------------- settings

# Known settings and their defaults. Unknown keys supplied via PATCH are
# accepted (forward compatibility) but not listed in GET defaults.
SETTING_DEFAULTS: dict[str, dict[str, Any]] = {
    "registration_open": {"value": True, "description": "Allow new account sign-ups"},
    # An organisation joining the platform to have its operation managed here
    # must own its workspace from the first minute; a viewer seat would leave
    # the first user unable to create even a location (RBAC blocks writes).
    "default_role": {"value": "owner", "description": "Seat granted to new sign-ups (owner by default)"},
    "announcement": {
        "value": "",
        "description": "Banner text shown to every signed-in user ('' = none)",
    },
    "support_email": {"value": "support@demandiq.app", "description": "Shown on error screens"},
    "maintenance_mode": {"value": False, "description": "Show maintenance banner and block writes"},
}


def get_platform_setting(db: Session, key: str) -> Any:
    """Read one platform setting with its default fallback (no auth needed)."""
    default = SETTING_DEFAULTS.get(key, {"value": None})["value"]
    try:
        row = db.execute(
            text("SELECT value FROM platform_settings WHERE key = :k"), {"k": key}
        ).first()
    except Exception:  # noqa: BLE001 — missing table pre-migration must not 500
        return default
    return row[0] if row is not None else default


def get_site_meta(db: Session) -> dict[str, Any]:
    """Public, unauthenticated view of the settings that affect clients."""
    return {
        "registration_open": bool(get_platform_setting(db, "registration_open")),
        "announcement": str(get_platform_setting(db, "announcement") or ""),
        "support_email": str(get_platform_setting(db, "support_email") or "support@demandiq.app"),
        "maintenance_mode": bool(get_platform_setting(db, "maintenance_mode")),
    }


@router.get("/settings")
def get_settings_all(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_admin(current_user)
    rows = db.execute(
        text("SELECT key, value, description, updated_at, updated_by FROM platform_settings")
    ).all()
    stored = {
        str(r[0]): {
            "value": r[1],
            "description": r[2],
            "updated_at": r[3].isoformat() if r[3] else None,
            "updated_by": str(r[4]) if r[4] else None,
        }
        for r in rows
    }
    merged = {}
    for key, default in SETTING_DEFAULTS.items():
        entry = stored.get(key)
        merged[key] = {
            "value": entry["value"] if entry else default["value"],
            "description": default["description"],
            "updated_at": entry["updated_at"] if entry else None,
            "updated_by": entry["updated_by"] if entry else None,
            "is_default": entry is None,
        }
    # pass through custom keys too
    for key, entry in stored.items():
        if key not in merged:
            merged[key] = {**entry, "is_default": False}
    return {"data": merged}


class SettingsPatch(BaseModel):
    settings: dict[str, Any]


@router.patch("/settings")
def patch_settings(
    payload: SettingsPatch,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_admin(current_user)
    if not payload.settings:
        raise _error(422, "validation_error", "No settings supplied")
    for key, value in payload.settings.items():
        db.execute(
            text(
                "INSERT INTO platform_settings (key, value, updated_at, updated_by) "
                "VALUES (:k, CAST(:v AS jsonb), now(), :uid) "
                "ON CONFLICT (key) DO UPDATE SET value = CAST(:v AS jsonb), updated_at = now(), updated_by = :uid"
            ),
            {"k": key, "v": __import__("json").dumps(value), "uid": str(current_user.id)},
        )
    db.flush()
    _audit(
        db,
        current_user,
        "site.settings.update",
        None,
        request,
        {"keys": sorted(payload.settings.keys())},
    )
    return {"updated": sorted(payload.settings.keys())}


# -------------------------------------------------------------------- audit


@router.get("/audit")
def site_audit(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    q: str | None = Query(default=None, description="Match the action or the actor"),
    action: str | None = Query(default=None, description="Action prefix, e.g. site.user"),
    client: str | None = Query(default=None, description="web | mobile"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    _require_admin(current_user)
    filters: list[Any] = []
    if action:
        filters.append(AuditLog.action.like(f"{action}%"))
    if client:
        filters.append(AuditLog.client == client)
    if q:
        like = f"%{q.lower()}%"
        actor_ids = [
            uuid.UUID(str(r[0]))
            for r in db.execute(
                text(
                    "SELECT id::text FROM users "
                    "WHERE lower(email) LIKE :like OR lower(name) LIKE :like"
                ),
                {"like": like},
            ).all()
        ]
        actor_match: list[Any] = [func.lower(AuditLog.action).like(like)]
        if actor_ids:
            actor_match.append(AuditLog.actor_id.in_(actor_ids))
        filters.append(or_(*actor_match))

    total = int(
        _optional_scalar(db, select(func.count()).select_from(AuditLog).where(*filters)) or 0
    )
    rows = db.scalars(
        select(AuditLog)
        .where(*filters)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    actor_ids = [r.actor_id for r in rows if r.actor_id]
    actors: dict[str, str] = {}
    if actor_ids:
        for r in db.execute(
            text("SELECT id::text, email::text FROM users WHERE id::text = ANY(:ids)"),
            {"ids": [str(a) for a in actor_ids]},
        ).all():
            actors[str(r[0])] = str(r[1])
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
        "data": [
            {
                "id": r.id,
                "action": r.action,
                "target": r.target,
                "actor_email": actors.get(str(r.actor_id)) if r.actor_id else None,
                "client": r.client,
                "meta": r.meta_data,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }


# ------------------------------------------------------------------ public meta


@router.get("/meta", tags=["site-admin"])
def site_meta(db: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
    """Public, unauthenticated view of the settings clients react to.

    The sign-up page reads ``registration_open`` before offering the form,
    and every signed-in surface reads ``announcement``/``maintenance_mode``
    for banner display. No privileged data is exposed here.
    """
    return get_site_meta(db)
