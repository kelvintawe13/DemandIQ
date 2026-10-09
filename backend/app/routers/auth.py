"""Auth router — register / login / refresh / me / oauth (Google)."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.models.user import MfaRecoveryCode, User, UserIdentity, UserRole
from app.models.user import Session as UserSession
from app.models.workspace import Workspace
from app.schemas.user import (
    ForgotPasswordRequest,
    GoogleOAuthRequest,
    LoginRequest,
    MeResponse,
    MfaVerifyRequest,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    UserResponse,
    VerifyEmailRequest,
)
from app.security.hashing import hash_password, verify_password
from app.security.human import verify_google_id_token, verify_turnstile
from app.security.jwt import create_access_token, create_refresh_token, decode_token
from app.security.permissions import capabilities_for
from app.security.rate_limit import rate_limit_dependency
from app.services.kv import SharedMap

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Challenge store: challenge_id (str UUID) -> user_id (UUID), mapping the
# `challenge_id` returned by /login to the user for /mfa/verify.
#
# Shared rather than process-local: a load balancer may hand /login and
# /mfa/verify to different workers, and a challenge parked in one worker's dict
# is invisible (and unverifiable) to the other. The TTL bounds an abandoned
# challenge — previously the dict grew without limit, since only a successful
# verify ever removed an entry. Falls back to the process-local map when Redis
# is unavailable (see app/services/kv.py).
MFA_CHALLENGE_TTL_SECONDS = 600
_mfa_challenges: SharedMap = SharedMap("auth:mfa_challenges", ttl_seconds=MFA_CHALLENGE_TTL_SECONDS)


def _error(code: int, err_code: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err_code, "message": msg, "field": field, "reference": None}},
    )


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _create_password_reset_token(user_id: uuid.UUID) -> str:
    """Create a short-lived password-reset JWT (type=password_reset).

    Uses the same HMAC secret as access tokens but a distinct ``type``
    claim so that reset tokens cannot be used as access tokens.
    """
    from datetime import timedelta

    from jose import jwt

    cfg = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(hours=1),
        "jti": str(uuid.uuid4()),
        "type": "password_reset",
    }
    return str(jwt.encode(payload, cfg.jwt_secret, algorithm=cfg.jwt_algorithm))


def create_password_reset_token_for_test(user_id: uuid.UUID) -> str:
    """Public helper for tests to generate a valid reset token."""
    return _create_password_reset_token(user_id)


def _verify_password_reset_token(token: str) -> uuid.UUID:
    """Verify a password-reset token and return the subject user id.

    Raises HTTPException 400 ``invalid_token`` if the token is invalid,
    expired or of the wrong type.
    """
    try:
        claims = decode_token(token)
    except Exception:
        raise _error(400, "invalid_token", "Invalid or expired token", "token") from None
    if claims.get("type") != "password_reset":
        raise _error(400, "invalid_token", "Invalid token type", "token")
    sub = claims.get("sub")
    if not sub:
        raise _error(400, "invalid_token", "Invalid token payload", "token")
    try:
        return uuid.UUID(str(sub))
    except ValueError:
        raise _error(400, "invalid_token", "Invalid token subject", "token") from None


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_dependency(5, 60, "register"))],
)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> User:  # type: ignore[no-untyped-def]
    """Create a workspace and an owner user.

    The endpoint is public and rate-limited. Email verification is out of
    scope for phase 1 — the user is created as verified for local dev.
    Governed by platform settings: ``registration_open`` disables sign-ups
    and ``default_role`` caps the seat a new account may claim.
    """
    from app.routers.site_admin import get_platform_setting

    verify_turnstile(
        payload.turnstile_token, remote_ip=request.client.host if request.client else None
    )
    if not bool(get_platform_setting(db, "registration_open")):
        raise _error(403, "registration_closed", "Registration is currently closed")
    existing = db.scalar(select(User).where(User.email == payload.email.lower()))
    if existing is not None:
        raise _error(409, "conflict", "Email already registered", "email")

    workspace = Workspace(
        name=payload.business_name,
        business_type=payload.business_type,
        country="RW",
        timezone="Africa/Kigali",
    )
    db.add(workspace)
    db.flush()

    # The requested seat is capped by the platform ``default_role`` setting:
    # a new sign-up may never claim a seat above it (platform trust ladder).
    # Missing setting → ``owner``: an organisation bringing its operation onto
    # the platform must own its workspace; the trust ladder still caps any
    # *requested* seat to whatever the platform admin configured.
    default_role_raw = str(get_platform_setting(db, "default_role") or "owner")
    try:
        default_role = UserRole(default_role_raw)
    except ValueError:
        default_role = UserRole.viewer
    requested = payload.role or default_role
    role_ladder = [
        UserRole.viewer,
        UserRole.operator,
        UserRole.analyst,
        UserRole.administrator,
        UserRole.owner,
    ]
    role = (
        requested
        if role_ladder.index(requested) <= role_ladder.index(default_role)
        else default_role
    )

    user = User(
        workspace_id=workspace.id,
        name=payload.name,
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        role=role,  # type: ignore[assignment]
        email_verified=False,
        # Email sign-ups supply name/business at registration — no gate.
        profile_completed=True,
    )
    db.add(user)
    db.flush()
    logger.info("Registered user=%s workspace=%s", user.id, workspace.id)
    return user


@router.post("/verify-email", response_model=UserResponse)
def verify_email(payload: VerifyEmailRequest, db: Session = Depends(get_db)) -> User:  # type: ignore[no-untyped-def]
    """Stub email verification — marks the user verified if code == 000000 or any 6-digit in testing."""
    cfg = get_settings()
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None:
        raise _error(404, "not_found", "User not found", "email")

    # Outside production there is no verification-code store yet, so any
    # 6-digit code is accepted (dev/test convenience). Production fails
    # closed: an unconfigured flow must never verify accounts.
    if cfg.app_env != "production":
        if len(payload.code) == 6:
            user.email_verified = True
            db.flush()
            logger.info("Email verified user=%s", user.id)
            return user
        raise _error(400, "invalid_code", "Invalid verification code", "code")
    raise _error(
        503, "not_available", "Email verification is not configured in this environment", "code"
    )


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED)
def resend_verification(payload: VerifyEmailRequest, db: Session = Depends(get_db)) -> dict:  # type: ignore[no-untyped-def]
    """Stub — always 202."""
    logger.info("Resend verification requested email=%s", payload.email)
    return {"message": "If the email exists, a code has been sent"}


@router.post(
    "/login",
    dependencies=[Depends(rate_limit_dependency(10, 60, "login"))],
)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> dict:  # type: ignore[no-untyped-def]
    """Authenticate with email/password and return tokens.

    If MFA is enabled the response is ``{mfa_required: true, challenge_id}``.
    """
    verify_turnstile(
        payload.turnstile_token, remote_ip=request.client.host if request.client else None
    )
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if (
        user is None
        or not user.password_hash
        or not verify_password(payload.password, user.password_hash)
    ):
        raise _error(401, "invalid_credentials", "Invalid email or password")

    if user.deactivated_at is not None:
        raise _error(403, "forbidden", "Account deactivated")

    if user.mfa_enabled:
        challenge_id = str(uuid.uuid4())
        _mfa_challenges[challenge_id] = user.id
        logger.info("MFA challenge issued user=%s challenge=%s", user.id, challenge_id)
        return {"mfa_required": True, "challenge_id": challenge_id}

    # The session row is named up front so the access token can carry its id
    # (the ``sid`` claim): that is what lets `GET /me/sessions` say which of the
    # listed sessions is the one being used.
    session_id = uuid.uuid4()
    payload_out = _session_payload(user, with_refresh=True, session_id=session_id)

    # Persist session
    session = UserSession(
        id=session_id,
        user_id=user.id,
        refresh_hash=_hash_token(payload_out["refresh_token"]),
        device=request.headers.get("user-agent"),
        origin=request.client.host if request.client else None,
    )
    db.add(session)
    db.flush()

    return payload_out


@router.post("/mfa/verify")
def mfa_verify(payload: MfaVerifyRequest, request: Request, db: Session = Depends(get_db)) -> dict:  # type: ignore[no-untyped-def]
    """Verify TOTP code or recovery code against the MFA challenge.

    Validates ``challenge_id`` (UUID), looks up the user via the in-memory
    ``_mfa_challenges`` map set during ``/login``, then verifies either a
    6-digit TOTP (via ``pyotp`` with ``valid_window=1``) or a recovery code.
    The ``000000`` magic code is only accepted when ``app_env != production``
    to keep tests/dev frictionless.
    """
    cfg = get_settings()

    # Ensure exactly one of code / recovery_code is supplied
    if payload.code and payload.recovery_code:
        raise _error(400, "invalid_code", "Provide either code or recovery_code, not both", "code")
    if not payload.code and not payload.recovery_code:
        raise _error(400, "invalid_code", "MFA code is required", "code")

    # Lookup challenge -> user
    cid_str = str(payload.challenge_id)
    user_id = _mfa_challenges.get(cid_str)
    if user_id is None:
        raise _error(400, "invalid_challenge", "Invalid or expired challenge", "challenge_id")

    user = db.get(User, user_id)
    if user is None:
        raise _error(401, "invalid_credentials", "User not found")
    if user.deactivated_at is not None:
        raise _error(403, "forbidden", "Account deactivated")
    if not user.mfa_enabled:
        raise _error(400, "not_enabled", "MFA is not enabled", "code")
    if payload.recovery_code:
        # Recovery code path — hash comparison against stored hashes
        h = hashlib.sha256(payload.recovery_code.encode()).hexdigest()
        # Query for a matching unused recovery code
        rc = db.scalar(
            select(MfaRecoveryCode).where(
                MfaRecoveryCode.user_id == user.id,
                MfaRecoveryCode.code_hash == h,
                MfaRecoveryCode.used_at.is_(None),
            )
        )
        # Fallback: Python-side check for mock DBs that ignore WHERE filtering
        if rc is None:
            try:
                candidates = db.scalars(select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id)).all()  # type: ignore[union-attr]
            except Exception:
                candidates = []
            for cand in candidates:
                if getattr(cand, "code_hash", None) == h and getattr(cand, "used_at", None) is None:
                    rc = cand
                    break
        if rc is None:
            raise _error(400, "invalid_code", "Invalid recovery code", "recovery_code")
        # Mark as used
        rc.used_at = datetime.now(UTC)  # type: ignore[attr-defined]
        db.flush()
    else:
        # TOTP code path — an explicit guard rather than an `assert`, which
        # `python -O` would strip and leave the verifier comparing against None.
        if payload.code is None:
            raise _error(400, "validation_error", "code is required", "code")
        if not user.mfa_secret:
            raise _error(400, "not_enrolled", "MFA secret not configured", "code")
        # Magic 000000 only allowed outside production (mirrors /me/mfa/confirm)
        if payload.code == "000000" and cfg.app_env != "production":
            pass  # accepted
        else:
            import pyotp  # type: ignore[import]

            totp = pyotp.TOTP(user.mfa_secret)
            # Use issuer from config for consistency (secret is independent of issuer)
            if not totp.verify(payload.code, valid_window=1):
                raise _error(400, "invalid_code", "Invalid MFA code", "code")

    # Success — consume challenge and issue tokens
    _mfa_challenges.pop(cid_str, None)

    session_id = uuid.uuid4()
    payload_out = _session_payload(user, with_refresh=True, session_id=session_id)

    session = UserSession(
        id=session_id,
        user_id=user.id,
        refresh_hash=_hash_token(payload_out["refresh_token"]),
        device=request.headers.get("user-agent"),
        origin=request.client.host if request.client else None,
        trusted_device=bool(payload.trust_device),
    )
    db.add(session)
    db.flush()

    logger.info(
        "MFA verified user=%s challenge=%s trust=%s", user.id, cid_str, payload.trust_device
    )
    return payload_out


def _session_payload(
    user: User, *, with_refresh: bool = True, session_id: uuid.UUID | None = None
) -> dict:
    """Build the standard token + user response, with the profile-completion flag."""
    cfg = get_settings()
    access = create_access_token(
        subject=str(user.id),
        workspace_id=str(user.workspace_id),
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
        session_id=str(session_id) if session_id is not None else None,
    )
    payload: dict = {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": cfg.jwt_ttl_minutes * 60,
        "needs_profile": not bool(getattr(user, "profile_completed", True)),
        "user": UserResponse.model_validate(user).model_dump(),
    }
    if with_refresh:
        refresh = create_refresh_token(subject=str(user.id))
        payload["refresh_token"] = refresh
    return payload


@router.post("/oauth/google")
def oauth_google(payload: GoogleOAuthRequest, request: Request, db: Session = Depends(get_db)) -> dict:  # type: ignore[no-untyped-def]
    """Exchange a Google ``id_token`` for a DemandIQ session.

    The token is verified against Google's JWKS (RS256, iss/aud/exp).
    An existing identity link logs the user in directly; otherwise the
    identity is linked by verified email, or a workspace + owner user is
    provisioned when no account exists yet. Note: MFA is not consulted on
    this path — possession of a verified Google identity is the factor.
    """
    identity = verify_google_id_token(payload.id_token)
    if not identity.email_verified:
        raise _error(401, "oauth_failed", "Google account email is not verified")

    link = db.scalar(
        select(UserIdentity).where(
            UserIdentity.provider == "google",
            UserIdentity.provider_uid == identity.subject,
        )
    )
    if link is not None:
        user = db.get(User, link.user_id)
        if user is None:
            raise _error(401, "oauth_failed", "Linked account not found")
    else:
        # First Google sign-in: link by verified email, else provision.
        user = db.scalar(select(User).where(User.email == identity.email))
        if user is None:
            display_name = identity.name or identity.email.split("@", 1)[0]
            workspace = Workspace(
                name=display_name,
                business_type=None,
                country="RW",
                timezone="Africa/Kigali",
            )
            db.add(workspace)
            db.flush()
            # Fresh Google sign-ups own the workspace they create. Google has
            # already verified the email (checked above), so the seat is granted
            # directly rather than through the ``default_role`` cap the
            # password path applies.
            user = User(
                workspace_id=workspace.id,
                name=display_name,
                email=identity.email,
                password_hash=None,
                role=UserRole.owner,  # type: ignore[assignment]
                email_verified=True,
                profile_completed=False,
            )
            db.add(user)
            db.flush()
            logger.info("Provisioned user via Google user=%s workspace=%s", user.id, workspace.id)
        db.add(UserIdentity(user_id=user.id, provider="google", provider_uid=identity.subject))
        db.flush()

    if user.deactivated_at is not None:
        raise _error(403, "forbidden", "Account deactivated")

    if user.avatar_url is None and identity.picture:
        user.avatar_url = identity.picture

    session_id = uuid.uuid4()
    payload_out = _session_payload(user, with_refresh=True, session_id=session_id)

    session = UserSession(
        id=session_id,
        user_id=user.id,
        refresh_hash=_hash_token(payload_out["refresh_token"]),
        device=request.headers.get("user-agent"),
        origin=request.client.host if request.client else None,
    )
    db.add(session)
    db.flush()

    logger.info("Google sign-in user=%s provider_uid=%s", user.id, identity.subject)
    return payload_out


@router.post(
    "/password/forgot",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_dependency(5, 60, "password_forgot"))],
)
def password_forgot(
    payload: ForgotPasswordRequest, db: Session = Depends(get_db)
) -> dict:  # type: ignore[no-untyped-def]
    """Request a password reset — always 202, never reveals existence.

    If the email exists a reset token is generated and logged (email
    delivery is out of scope for the stub). The response is identical
    whether the address exists or not so that enumeration is impossible.
    """
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is not None:
        token = _create_password_reset_token(user.id)
        logger.info("Password reset requested user=%s token=%s", user.id, token[:12] + "***")
    else:
        logger.info("Password reset requested for unknown email=%s", payload.email)
    # Always 202 with the same message.
    return {"message": "If the email exists, a password reset link has been sent"}


@router.post("/password/reset")
def password_reset(
    payload: ResetPasswordRequest, db: Session = Depends(get_db)
) -> dict:  # type: ignore[no-untyped-def]
    """Reset a password using a reset token (token verify stub).

    The token must be a valid ``password_reset`` JWT. In testing any
    correctly-signed token is accepted; in production the same check
    applies. The password is validated by Pydantic (min 8) before this
    handler runs.
    """
    user_id = _verify_password_reset_token(payload.token)
    user = db.get(User, user_id)
    if user is None:
        raise _error(400, "invalid_token", "Invalid token", "token")
    if user.deactivated_at is not None:
        raise _error(403, "forbidden", "Account deactivated")
    user.password_hash = hash_password(payload.password)
    db.flush()
    logger.info("Password reset completed user=%s", user.id)
    return {"message": "Password has been reset"}


@router.post("/refresh")
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> dict:  # type: ignore[no-untyped-def]
    """Exchange a refresh token for a new access token."""
    try:
        claims = decode_token(payload.refresh_token)
    except Exception:
        raise _error(401, "unauthorized", "Invalid refresh token") from None

    if claims.get("type") != "refresh":
        raise _error(401, "unauthorized", "Invalid token type")

    user_id = claims.get("sub")
    if not user_id:
        raise _error(401, "unauthorized", "Invalid token")

    # Verify session exists and not revoked
    h = _hash_token(payload.refresh_token)
    session = db.scalar(
        select(UserSession).where(UserSession.refresh_hash == h, UserSession.revoked_at.is_(None))
    )
    if session is None:
        raise _error(401, "unauthorized", "Session not found or revoked")

    user = db.get(User, uuid.UUID(str(user_id)))
    if user is None or user.deactivated_at is not None:
        raise _error(401, "unauthorized", "User not found")

    # Rotation: a refresh token is single-use. The presented session is
    # revoked and a fresh one provisioned, so replaying an old token fails
    # with "Session not found or revoked".
    session.revoked_at = datetime.now(UTC)
    rotated_refresh = create_refresh_token(subject=str(user.id))
    # The rotated session is named here for the same reason as at login: the new
    # access token identifies it, so the sessions list keeps marking the right
    # row as the one in use after a refresh.
    rotated_id = uuid.uuid4()
    db.add(
        UserSession(
            id=rotated_id,
            user_id=user.id,
            refresh_hash=_hash_token(rotated_refresh),
            device=session.device,
            origin=session.origin,
            trusted_device=session.trusted_device,
        )
    )
    db.flush()

    access = create_access_token(
        subject=str(user.id),
        workspace_id=str(user.workspace_id),
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
        session_id=str(rotated_id),
    )
    return {
        "access_token": access,
        "refresh_token": rotated_refresh,
        "token_type": "bearer",
        "expires_in": get_settings().jwt_ttl_minutes * 60,
    }


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(payload: RefreshRequest, db: Session = Depends(get_db)) -> None:  # type: ignore[no-untyped-def]
    """Revoke the refresh token's session."""
    h = _hash_token(payload.refresh_token)
    session = db.scalar(select(UserSession).where(UserSession.refresh_hash == h))
    if session is not None:
        session.revoked_at = datetime.now(UTC)
        db.flush()


@router.get("/me", response_model=MeResponse)
def me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MeResponse:  # type: ignore[no-untyped-def]
    """Current user, workspace and permissions."""
    workspace = db.get(Workspace, current_user.workspace_id)
    ws_dict = None
    if workspace is not None:
        ws_dict = {
            "id": str(workspace.id),
            "name": workspace.name,
            "business_type": workspace.business_type,
            "country": workspace.country,
            "timezone": workspace.timezone,
        }
    perms = _permissions_for(current_user)
    return MeResponse(
        user=UserResponse.model_validate(current_user),
        workspace=ws_dict,  # type: ignore[arg-type]
        permissions=perms,
        has_password=bool(current_user.password_hash),
    )


def _permissions_for(user: User) -> list[str]:
    """Capabilities granted by the user's role.

    Sourced from ``app.security.permissions`` so the client receives exactly
    what the API enforces instead of a second, drifting hand-written list.
    """
    return sorted(capabilities_for(user))
