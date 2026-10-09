"""User and auth schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.user import UserRole


class RegisterRequest(BaseModel):
    """Registration payload per ``API_ENDPOINTS.md``."""

    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: UserRole | None = Field(default=UserRole.owner)
    business_name: str = Field(min_length=1, max_length=200)
    business_type: str | None = Field(default=None)
    location_count: int | None = Field(default=None, ge=1)
    daily_covers: int | None = Field(default=None, ge=0)
    data_sources: list[str] | None = Field(default=None)
    turnstile_token: str | None = Field(default=None)


class LoginRequest(BaseModel):
    """Login payload."""

    email: EmailStr
    password: str = Field(min_length=1)
    turnstile_token: str | None = Field(default=None)


class VerifyEmailRequest(BaseModel):
    """Email verification payload."""

    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class ResendVerificationRequest(BaseModel):
    """Resend email verification."""

    email: EmailStr


class MfaVerifyRequest(BaseModel):
    """MFA challenge verification."""

    challenge_id: uuid.UUID = Field(description="MFA challenge identifier (UUID)")
    code: str | None = Field(default=None, pattern=r"^\d{6}$", description="6-digit TOTP code")
    recovery_code: str | None = Field(default=None, min_length=8, max_length=32)
    trust_device: bool = Field(default=False)


class GoogleOAuthRequest(BaseModel):
    """Google OAuth id_token exchange."""

    id_token: str = Field(min_length=10)


class ProfileCompleteRequest(BaseModel):
    """First-time profile completion (fresh OAuth-provisioned accounts)."""

    name: str = Field(min_length=1, max_length=200)
    job_title: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, pattern=r"^\+?[0-9\s\-]{7,32}$")


class RefreshRequest(BaseModel):
    """Refresh token exchange."""

    refresh_token: str


class UserResponse(BaseModel):
    """Public user shape."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    email: str
    role: UserRole
    phone: str | None = None
    job_title: str | None
    avatar_url: str | None
    mfa_enabled: bool
    email_verified: bool
    # False until a fresh OAuth-provisioned account completes its profile.
    profile_completed: bool = True
    # Platform-level administrator (manages all workspaces & site settings),
    # independent of the workspace role above. None (unflushed ORM instances
    # carry no column default) coerces to False.
    is_site_admin: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("is_site_admin", mode="before")
    @classmethod
    def _coerce_site_admin(cls, v: object) -> bool:
        """Treat missing/None (unflushed instances) as not-a-site-admin."""
        return v is True or v == "true"


class TokenPair(BaseModel):
    """JWT pair returned on successful auth."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 — OAuth token *type*, not a secret
    expires_in: int = Field(description="Access token lifetime in seconds")


class AuthResponse(BaseModel):
    """Successful authentication response."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 — OAuth token *type*, not a secret
    user: UserResponse


class MfaChallengeResponse(BaseModel):
    """Returned when MFA is required."""

    mfa_required: Literal[True] = True
    challenge_id: str


class MeResponse(BaseModel):
    """``GET /auth/me`` response."""

    user: UserResponse
    workspace: dict | None = Field(default=None)
    permissions: list[str] = Field(default_factory=list)
    # False for OAuth-only accounts (Google), which have no password to
    # confirm. Lets the client hide the password field on account-erasure
    # instead of showing a box the account cannot fill in.
    has_password: bool = False


class ForgotPasswordRequest(BaseModel):
    """``POST /auth/password/forgot`` — always 202, never reveals existence."""

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """``POST /auth/password/reset`` — token + new password."""

    token: str = Field(min_length=10, description="Password-reset token")
    password: str = Field(min_length=8, max_length=128, description="New password")
