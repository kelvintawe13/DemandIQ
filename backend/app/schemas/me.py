"""Schemas for ``/api/v1/me`` endpoints (Pydantic v2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field

# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


class ProfileUpdateRequest(BaseModel):
    """PATCH /me/profile body — all fields optional."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: EmailStr | None = Field(default=None)
    phone: str | None = Field(default=None, max_length=32)
    job_title: str | None = Field(default=None, max_length=200)
    avatar_url: str | None = Field(default=None, max_length=2000)


class ProfileResponse(BaseModel):
    """Profile read shape — mirrors ``UserResponse`` plus phone."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    email: str
    phone: str | None = None
    job_title: str | None = None
    avatar_url: str | None = None
    role: str
    mfa_enabled: bool
    email_verified: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AvatarResponse(BaseModel):
    """POST /me/avatar response."""

    url: str
    avatar_url: str


# ---------------------------------------------------------------------------
# Password
# ---------------------------------------------------------------------------


class PasswordChangeRequest(BaseModel):
    """POST /me/password body."""

    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


class PasswordChangeResponse(BaseModel):
    """Password change confirmation."""

    message: str = "Password updated"


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


class PreferencesRequest(BaseModel):
    """PATCH /me/preferences body."""

    theme: Literal["light", "dark", "system"] | None = Field(default=None)
    language: str | None = Field(default=None, max_length=10)
    density: Literal["comfortable", "compact"] | None = Field(default=None)
    default_location: uuid.UUID | None = Field(default=None)
    default_location_id: uuid.UUID | None = Field(default=None)


class PreferencesResponse(BaseModel):
    """GET /me/preferences response."""

    theme: str = Field(default="light")
    language: str = Field(default="en")
    density: str = Field(default="comfortable")
    default_location: uuid.UUID | None = None
    default_location_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


class NotificationSettingsRequest(BaseModel):
    """PATCH /me/notifications body — per-channel toggles."""

    email_digest: bool | None = Field(default=None)
    push_alerts: bool | None = Field(default=None)
    in_app_alerts: bool | None = Field(default=None)
    weekly_report: bool | None = Field(default=None)
    email_enabled: bool | None = Field(default=None)
    push_enabled: bool | None = Field(default=None)
    alerts_enabled: bool | None = Field(default=None)
    digest_enabled: bool | None = Field(default=None)


class NotificationSettingsResponse(BaseModel):
    """GET /me/notifications response."""

    email_digest: bool = True
    push_alerts: bool = True
    in_app_alerts: bool = True
    weekly_report: bool = False
    email_enabled: bool = True
    push_enabled: bool = True
    alerts_enabled: bool = True
    digest_enabled: bool = True

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# MFA
# ---------------------------------------------------------------------------


class MfaEnrollResponse(BaseModel):
    """POST /me/mfa/enroll response."""

    secret: str
    provisioning_uri: str
    otpauth_url: str | None = None


class MfaConfirmRequest(BaseModel):
    """POST /me/mfa/confirm body."""

    code: str = Field(
        min_length=6, max_length=6, pattern=r"^\d{6}$", description="6-digit TOTP code"
    )


class MfaRecoveryCodesResponse(BaseModel):
    """Recovery codes — shown once."""

    recovery_codes: list[str]
    count: int | None = None


class MfaStatusResponse(BaseModel):
    """Generic MFA status."""

    mfa_enabled: bool
    message: str | None = None


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class SessionResponse(BaseModel):
    """Single session row."""

    id: uuid.UUID
    device: str | None = None
    #: Where the sign-in was made from, as recorded by the auth router: the
    #: client address (or a country code, in a deployment that resolves one).
    origin: str | None = None
    location: str | None = None
    trusted_device: bool = False
    last_seen_at: datetime
    created_at: datetime | None = None
    current: bool = False

    model_config = {"from_attributes": True}


class SessionsListResponse(BaseModel):
    """GET /me/sessions response."""

    data: list[SessionResponse]
    count: int | None = None


# ---------------------------------------------------------------------------
# Identities
# ---------------------------------------------------------------------------


class IdentityResponse(BaseModel):
    """One linked sign-in method, as `GET /me/identities` returns it.

    The provider's own subject id is deliberately absent: it is the internal
    binding (an ``user_identities.provider_uid``), not something a client acts
    on. What a screen shows beside the provider is when it was linked.
    """

    provider: str
    linked_at: datetime | None = None

    model_config = {"from_attributes": True}
