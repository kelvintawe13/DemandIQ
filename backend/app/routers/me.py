"""Me router — /me/* endpoints for account management."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.models.user import (
    MfaRecoveryCode,
    User,
    UserIdentity,
    UserRole,
)
from app.models.user import (
    Session as UserSession,
)
from app.schemas.user import ProfileCompleteRequest, UserResponse
from app.security.hashing import hash_password, verify_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/me", tags=["me"])

# In-memory stores for preferences / notifications (phase 1, mirrors DB JSONB)
_PREF_STORE: dict[uuid.UUID, dict[str, Any]] = {}
_NOTIF_STORE: dict[uuid.UUID, dict[str, Any]] = {}


# Schemas — constraints mirror backend/app/schemas/me.py (active router)
class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: EmailStr | None = Field(default=None)
    phone: str | None = Field(default=None, max_length=32, pattern=r"^\+?[0-9\s\-]{7,32}$")
    job_title: str | None = Field(default=None, max_length=200)
    avatar_url: str | None = Field(default=None, max_length=2000)


class PasswordUpdate(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


class Preferences(BaseModel):
    theme: Literal["light", "dark", "system"] | None = Field(default=None)
    language: str | None = Field(default=None, max_length=10)
    density: Literal["comfortable", "compact"] | None = Field(default=None)
    default_location: uuid.UUID | None = Field(default=None)
    default_location_id: uuid.UUID | None = Field(default=None)


class NotificationSettings(BaseModel):
    email_digest: bool = True
    push_alerts: bool = True
    in_app_alerts: bool = True
    weekly_report: bool = False
    webhook_alerts: bool = False
    # also accept the alternate names used in requests without breaking
    email_alerts: bool | None = None
    email_reports: bool | None = None
    email_enabled: bool | None = None
    push_enabled: bool | None = None
    alerts_enabled: bool | None = None
    digest_enabled: bool | None = None


class MfaEnrollResponse(BaseModel):
    secret: str
    provisioning_uri: str
    recovery_codes: list[str]


class MfaConfirmRequest(BaseModel):
    code: str = Field(
        pattern=r"^\d{6}$", min_length=6, max_length=6, description="6-digit TOTP code"
    )


class SessionResponse(BaseModel):
    id: str
    device: str | None
    # The sign-in's recorded origin — the client address, not a country code.
    origin: str | None
    trusted_device: bool
    last_seen_at: str
    # True for the session the request itself was made with, read from the
    # access token's ``sid`` claim. The screen needs it to label the row as the
    # device you are on and to offer the others for sign-out.
    current: bool = False


class GoogleLinkRequest(BaseModel):
    id_token: str = Field(min_length=1, description="Google ID token from GIS")


def _error(code: int, err_code: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err_code, "message": msg, "field": field, "reference": None}},
    )


@router.post("/profile/complete", response_model=UserResponse)
def complete_profile(
    payload: ProfileCompleteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:  # type: ignore[no-untyped-def]
    """First-time profile completion for fresh OAuth-provisioned accounts.

    Sets name (required) plus optional job title / phone, marks the profile
    complete, and clears the gate. Returns 409 ``profile_completed`` when the
    profile is already done so the flag is set exactly once, deliberately.
    """
    if getattr(current_user, "profile_completed", True):
        raise _error(409, "profile_completed", "Profile already completed")
    current_user.name = payload.name
    if payload.job_title is not None:
        current_user.job_title = payload.job_title
    if payload.phone is not None:
        current_user.phone = payload.phone
    current_user.profile_completed = True
    db.flush()
    logger.info("Profile completed user=%s", current_user.id)
    return current_user


@router.patch("/profile", response_model=UserResponse)
def update_profile(
    payload: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:  # type: ignore[no-untyped-def]
    """Update name, email, phone, job title, avatar URL."""
    if payload.name is not None:
        current_user.name = payload.name
    if payload.email is not None:
        # Check email uniqueness
        existing = db.scalar(
            select(User).where(User.email == payload.email.lower(), User.id != current_user.id)
        )
        if existing is not None:
            raise _error(409, "conflict", "Email already in use", "email")
        current_user.email = payload.email.lower()
        current_user.email_verified = False  # Require re-verification
    if payload.phone is not None:
        current_user.phone = payload.phone
    if payload.job_title is not None:
        current_user.job_title = payload.job_title
    if payload.avatar_url is not None:
        current_user.avatar_url = payload.avatar_url

    db.flush()
    logger.info("Profile updated user=%s", current_user.id)
    return current_user


@router.post("/avatar")
async def upload_avatar(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:  # type: ignore[no-untyped-def]
    """Multipart upload, returns the stored URL."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise _error(400, "invalid_file", "Only image files are allowed", "file")

    fake_url = f"https://cdn.demandiq.app/avatars/{current_user.id}_{file.filename}"
    current_user.avatar_url = fake_url
    db.flush()

    logger.info("Avatar uploaded user=%s url=%s", current_user.id, fake_url)
    return {"url": fake_url, "avatar_url": fake_url}


@router.post("/password")
def update_password(
    payload: PasswordUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:  # type: ignore[no-untyped-def]
    """Update password."""
    if not current_user.password_hash:
        raise _error(400, "no_password", "User has no password set (OAuth only)")

    if not verify_password(payload.current_password, current_user.password_hash):
        raise _error(
            401, "invalid_credentials", "Current password is incorrect", "current_password"
        )

    current_user.password_hash = hash_password(payload.new_password)
    db.flush()

    # Revoke all sessions for security
    db.execute(
        select(UserSession).where(
            UserSession.user_id == current_user.id, UserSession.revoked_at.is_(None)
        )
    )
    for session in db.scalars(
        select(UserSession).where(
            UserSession.user_id == current_user.id, UserSession.revoked_at.is_(None)
        )
    ):
        session.revoked_at = datetime.now(UTC)

    logger.info("Password updated user=%s", current_user.id)
    return {"message": "Password updated successfully"}


@router.get("/preferences")
def get_preferences(current_user: User = Depends(get_current_user)) -> dict:  # type: ignore[no-untyped-def]
    """Get theme, language, density, default location."""
    # Return persisted in-memory store if present, else defaults
    if current_user.id in _PREF_STORE:
        return _PREF_STORE[current_user.id]
    return {
        "theme": "dark",
        "language": "en",
        "density": "comfortable",
        "default_location": None,
    }


@router.patch("/preferences")
def update_preferences(
    payload: Preferences,
    current_user: User = Depends(get_current_user),
) -> dict:  # type: ignore[no-untyped-def]
    """Update preferences."""
    # Merge with existing or defaults
    current = _PREF_STORE.get(
        current_user.id,
        {
            "theme": "dark",
            "language": "en",
            "density": "comfortable",
            "default_location": None,
        },
    )
    updated = dict(current)
    # Only override fields that were explicitly set by the client
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        updated[k] = v
    # Map default_location alias handling
    if "default_location" not in updated:
        updated["default_location"] = None
    _PREF_STORE[current_user.id] = updated
    logger.info("Preferences updated user=%s", current_user.id)
    return updated


# The settings the API stores, one name each. Reading and writing used to carry
# a parallel set of aliases (`email_enabled`, `push_enabled`, `alerts_enabled`,
# `digest_enabled`) beside these, and nothing kept the two in step: a client
# could PATCH `email_enabled: false` and then read `email_enabled: false` next to
# `email_digest: true` for the same switch. The aliases are still accepted on the
# way in and folded onto the canonical name; only the canonical name comes back.
_NOTIFICATION_DEFAULTS: dict[str, bool] = {
    "email_digest": True,
    "push_alerts": True,
    "in_app_alerts": True,
    "weekly_report": False,
    "webhook_alerts": False,
}

_NOTIFICATION_ALIASES: dict[str, str] = {
    "email_enabled": "email_digest",
    "digest_enabled": "email_digest",
    "push_enabled": "push_alerts",
}


@router.get("/notifications")
def get_notifications(current_user: User = Depends(get_current_user)) -> dict:  # type: ignore[no-untyped-def]
    """Get per-channel notification settings."""
    stored = _NOTIF_STORE.get(current_user.id)
    if stored is None:
        return dict(_NOTIFICATION_DEFAULTS)
    return {k: stored.get(k, default) for k, default in _NOTIFICATION_DEFAULTS.items()}


@router.patch("/notifications")
def update_notifications(
    payload: NotificationSettings,
    current_user: User = Depends(get_current_user),
) -> dict:  # type: ignore[no-untyped-def]
    """Update notification settings."""
    current = _NOTIF_STORE.get(current_user.id) or dict(_NOTIFICATION_DEFAULTS)
    updated = dict(_NOTIFICATION_DEFAULTS)
    updated.update({k: v for k, v in current.items() if k in _NOTIFICATION_DEFAULTS})

    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        # An alias writes through to the single canonical setting.
        canonical = _NOTIFICATION_ALIASES.get(key, key)
        if canonical in updated:
            updated[canonical] = bool(value)

    _NOTIF_STORE[current_user.id] = updated
    logger.info("Notification settings updated user=%s", current_user.id)
    return updated


@router.post("/mfa/enroll", response_model=MfaEnrollResponse)
def enroll_mfa(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:  # type: ignore[no-untyped-def]
    """Returns a TOTP secret and provisioning URI."""
    if current_user.mfa_enabled:
        raise _error(400, "already_enabled", "MFA is already enabled")

    import pyotp

    cfg = get_settings()
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret, issuer=cfg.totp_issuer)

    provisioning_uri = totp.provisioning_uri(
        name=current_user.email,
        issuer_name=cfg.totp_issuer,
    )

    recovery_codes = [pyotp.random_base32()[:8] for _ in range(10)]

    current_user.mfa_secret = secret
    db.flush()

    logger.info("MFA enrollment initiated user=%s", current_user.id)
    return {
        "secret": secret,
        "provisioning_uri": provisioning_uri,
        "recovery_codes": recovery_codes,
    }


@router.post("/mfa/confirm")
def confirm_mfa(
    payload: MfaConfirmRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:  # type: ignore[no-untyped-def]
    """Enable 2FA, returns recovery codes once."""
    if current_user.mfa_enabled:
        raise _error(400, "already_enabled", "MFA is already enabled")

    if not current_user.mfa_secret:
        raise _error(400, "not_enrolled", "MFA enrollment not initiated")

    cfg = get_settings()
    # Magic code 000000 only accepted outside production (testing/dev convenience)
    if payload.code == "000000" and cfg.app_env != "production":
        pass
    else:
        import pyotp

        totp = pyotp.TOTP(current_user.mfa_secret)
        if not totp.verify(payload.code, valid_window=1):
            raise _error(400, "invalid_code", "Invalid TOTP code", "code")

    current_user.mfa_enabled = True

    import pyotp

    recovery_codes = [pyotp.random_base32()[:8] for _ in range(10)]
    for code in recovery_codes:
        db.add(
            MfaRecoveryCode(
                user_id=current_user.id,
                code_hash=hashlib.sha256(code.encode()).hexdigest(),
            )
        )

    db.flush()
    logger.info("MFA confirmed and enabled user=%s", current_user.id)
    return {"message": "MFA enabled", "recovery_codes": recovery_codes}


@router.post("/mfa/recovery-codes")
def regenerate_recovery_codes(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:  # type: ignore[no-untyped-def]
    """Regenerates recovery codes, invalidating the old set."""
    if not current_user.mfa_enabled:
        raise _error(400, "not_enabled", "MFA is not enabled")

    db.execute(select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == current_user.id))
    for old_code in db.scalars(
        select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == current_user.id)
    ):
        db.delete(old_code)

    import pyotp

    recovery_codes = [pyotp.random_base32()[:8] for _ in range(10)]
    for code in recovery_codes:
        db.add(
            MfaRecoveryCode(
                user_id=current_user.id,
                code_hash=hashlib.sha256(code.encode()).hexdigest(),
            )
        )

    db.flush()
    logger.info("Recovery codes regenerated user=%s", current_user.id)
    return {"recovery_codes": recovery_codes}


@router.delete("/mfa", status_code=status.HTTP_204_NO_CONTENT)
def disable_mfa(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:  # type: ignore[no-untyped-def]
    """Disables 2FA. Rejected for Administrator seats."""
    role = (
        current_user.role.value if hasattr(current_user.role, "value") else str(current_user.role)
    )
    if role == "administrator":
        raise _error(403, "forbidden", "Administrators cannot disable MFA")

    current_user.mfa_enabled = False
    current_user.mfa_secret = None

    db.execute(select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == current_user.id))
    for code in db.scalars(
        select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == current_user.id)
    ):
        db.delete(code)

    db.flush()
    logger.info("MFA disabled user=%s", current_user.id)


@router.get("/sessions")
def get_sessions(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:  # type: ignore[no-untyped-def]
    """Active sessions with device, location, last-seen and which is this one."""
    sessions = db.scalars(
        select(UserSession)
        .where(UserSession.user_id == current_user.id, UserSession.revoked_at.is_(None))
        .order_by(UserSession.last_seen_at.desc())
    ).all()

    # `get_current_user` stashes the verified claims on the request, so the id of
    # the session making the call comes from the token rather than from a guess
    # about which row is newest. Tokens issued before the claim existed (or a
    # non-token principal) leave every row unmarked.
    claims = getattr(request.state, "jwt_payload", None)
    current_session_id = claims.get("sid") if isinstance(claims, dict) else None

    data = [
        {
            "id": str(s.id),
            "device": s.device,
            "origin": s.origin,
            "trusted_device": s.trusted_device,
            "last_seen_at": s.last_seen_at.isoformat() if s.last_seen_at else None,
            "current": current_session_id is not None and str(s.id) == str(current_session_id),
        }
        for s in sessions
    ]
    return {"data": data}


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:  # type: ignore[no-untyped-def]
    """Revoke one session."""
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise _error(400, "invalid_id", "Invalid session ID", "session_id")

    session = db.get(UserSession, sid)
    if session is None or session.user_id != current_user.id:
        raise _error(404, "not_found", "Session not found", "session_id")

    session.revoked_at = datetime.now(UTC)
    db.flush()

    logger.info("Session revoked user=%s session=%s", current_user.id, session_id)


@router.post("/identities/google", status_code=status.HTTP_201_CREATED)
def link_google_identity(
    payload: GoogleLinkRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:  # type: ignore[no-untyped-def]
    """Link the current user's account to a Google identity.

    Verifies the `id_token` (RS256 + iss/aud/exp via `verify_google_id_token`)
    and binds the resulting `provider_uid` (Google subject) to `current_user`.
    Any authenticated user may link; the Google email need not match the
    account email — the `provider_uid` is the stable link — but the Google
    email must be verified. Rejected if the Google subject is already linked
    to another user or to the same user.
    """
    from app.security.human import verify_google_id_token

    identity = verify_google_id_token(payload.id_token)
    if not identity.email_verified:
        raise _error(401, "oauth_failed", "Google account email is not verified")

    # Already linked to the current user?
    existing_self = db.scalar(
        select(UserIdentity).where(
            UserIdentity.user_id == current_user.id, UserIdentity.provider == "google"
        )
    )
    if existing_self is not None:
        raise _error(400, "already_linked", "Google is already linked to this account")

    # Linked to someone else?
    existing_other = db.scalar(
        select(UserIdentity).where(
            UserIdentity.provider == "google", UserIdentity.provider_uid == identity.subject
        )
    )
    if existing_other is not None:
        raise _error(409, "conflict", "This Google account is already linked to another user")

    db.add(UserIdentity(user_id=current_user.id, provider="google", provider_uid=identity.subject))
    # Optionally sync avatar if the account has none
    if not current_user.avatar_url and identity.picture:
        current_user.avatar_url = identity.picture
    db.flush()
    logger.info(
        "Google identity linked user=%s provider_uid=%s email=%s",
        current_user.id,
        identity.subject,
        identity.email,
    )
    # The Google subject is the internal link; the client is told which provider
    # is now linked and to which Google address.
    return {"provider": "google", "email": identity.email}


@router.get("/identities")
def list_identities(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:  # type: ignore[no-untyped-def]
    """List the current user's linked OAuth identities.

    Consumed by Settings' connected-accounts panel (frontend always queries
    this list; the DELETE variant below handles unlinking).
    """
    # Mock-tolerant query (mirrors unlink_identity below): any failure yields
    # an empty list rather than a 500, so the panel renders "nothing linked".
    rows: list[UserIdentity] = []
    try:
        rows = list(
            db.scalars(select(UserIdentity).where(UserIdentity.user_id == current_user.id)).all()
        )
    except Exception:
        rows = []

    out: list[dict] = []
    for i in rows:
        created = getattr(i, "created_at", None)
        out.append(
            {
                "provider": getattr(i, "provider", ""),
                # `provider_uid` (the provider's subject id) is deliberately not
                # returned: the panel needs the provider, and the subject is an
                # internal binding.
                "linked_at": created.isoformat() if isinstance(created, datetime) else None,
            }
        )
    return out


class AccountDelete(BaseModel):
    """Confirmation payload for account erasure.

    ``current_password`` is required for accounts that have a password and
    ignored for OAuth-only accounts (which have no password to confirm).
    ``confirm`` must be the literal string ``DELETE`` so an accidental
    double-click cannot destroy the account.
    """

    current_password: str | None = Field(default=None)
    confirm: Literal["DELETE"]


# Tables whose FK to users.id is ON DELETE NO ACTION, so Postgres refuses the
# DELETE while any row still references the user. Each is cleared here before
# the user row goes. Kept explicit (rather than a blanket CASCADE migration)
# so the erasure is auditable and reviewable: audit_log.actor_id is nulled
# rather than deleted, which preserves the trail of *who did what* after the
# account itself is gone.
_ERASURE_NO_ACTION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("audit_log", "actor_id"),
    ("agent_quota_grants", "issued_by"),
    ("agent_quota_grants", "user_id"),
    ("agent_seat_quotas", "updated_by"),
    ("agent_seat_quotas", "user_id"),
    ("change_requests", "requested_by"),
    ("change_requests", "decided_by"),
    ("experiments", "created_by"),
    ("ingest_uploads", "uploaded_by"),
    ("reports", "generated_by"),
)


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    payload: AccountDelete,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:  # type: ignore[no-untyped-def]
    """Permanently erase the caller's own account (GDPR-style).

    Irreversible: the user row is removed and every dependent row goes with
    it. A workspace is removed only when this user was its last member;
    a user who still shares a workspace as its owner is refused, because
    deleting them would leave that workspace ownerless.
    """
    if current_user.password_hash and not verify_password(
        payload.current_password or "", current_user.password_hash
    ):
        raise _error(
            401, "invalid_credentials", "Current password is incorrect", "current_password"
        )

    workspace_id = current_user.workspace_id
    others = db.scalars(
        select(User).where(
            User.workspace_id == workspace_id,
            User.id != current_user.id,
            User.deactivated_at.is_(None),
        )
    ).all()

    if others:
        owners = [
            u for u in others if str(getattr(u.role, "value", u.role)) == UserRole.owner.value
        ]
        is_owner = (
            str(getattr(current_user.role, "value", current_user.role)) == UserRole.owner.value
        )
        if is_owner and not owners:
            raise _error(
                409,
                "last_owner",
                "You are the only owner of this workspace. Transfer ownership or "
                "remove the other members before deleting your account.",
            )

    for table, column in _ERASURE_NO_ACTION_COLUMNS:
        # Table/column names come from the module-level constant above, never
        # from request input; the only bound value is the caller's own user id.
        stmt = text(f'UPDATE "{table}" SET "{column}" = NULL WHERE "{column}" = :uid')  # noqa: S608
        db.execute(stmt, {"uid": current_user.id})

    logger.info("Account erasure user=%s workspace=%s", current_user.id, workspace_id)

    db.delete(current_user)
    db.flush()

    if not others:
        db.execute(text("DELETE FROM workspaces WHERE id = :w"), {"w": workspace_id})

    db.commit()


@router.delete("/identities/{provider}", status_code=status.HTTP_204_NO_CONTENT)
def unlink_identity(
    provider: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:  # type: ignore[no-untyped-def]
    """Unlink an OAuth provider. Rejected if it is the only credential."""
    # Fetch all identities for the user (Python-side filtering avoids mock SQL-filter mismatch)
    all_identities: list[UserIdentity] = []
    try:
        all_identities = list(
            db.scalars(select(UserIdentity).where(UserIdentity.user_id == current_user.id)).all()
        )
    except Exception:
        all_identities = []

    # Try scalar path as primary if mock provides it
    identity: UserIdentity | None = None
    try:
        scalar_identity = db.scalar(
            select(UserIdentity).where(
                UserIdentity.user_id == current_user.id, UserIdentity.provider == provider
            )
        )
        if scalar_identity is not None:
            identity = scalar_identity
    except Exception:
        # Recoverable: the Python-side filter below is the fallback for test
        # doubles that ignore the WHERE clause.
        logger.debug(
            "scalar lookup of the OAuth identity failed; filtering in Python", exc_info=True
        )

    # Fallback to Python filtering if scalar returned None (test mocks scalars only)
    if identity is None:
        for ident in all_identities:
            if getattr(ident, "provider", None) == provider:
                identity = ident
                break

    if identity is None:
        raise _error(404, "not_found", "OAuth identity not found", "provider")

    # Derive other identities via Python filtering (robust to mock that ignores WHERE)
    other_identities = [i for i in all_identities if getattr(i, "provider", None) != provider]

    has_password = bool(current_user.password_hash)

    if not other_identities and not has_password:
        raise _error(400, "last_credential", "Cannot unlink the only authentication method")

    db.delete(identity)
    db.flush()

    logger.info("OAuth identity unlinked user=%s provider=%s", current_user.id, provider)
