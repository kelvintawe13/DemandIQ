"""User, identity, session and recovery code models."""

from __future__ import annotations

import datetime
import enum
import uuid

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, MutableJSONB


class UserRole(enum.StrEnum):
    """Application roles. Mirrors ``user_role`` enum in schema.sql.

    Uses :class:`enum.StrEnum` (Python 3.11+) rather than ``str, Enum``: the
    members already equal their own names, nothing calls ``str()``/``format()``
    on a member, and StrEnum keeps the str-mixin hashing that ``ROLE_LABELS``
    dict lookups rely on — so serialisation is unchanged.
    """

    owner = "owner"
    administrator = "administrator"
    analyst = "analyst"
    operator = "operator"
    viewer = "viewer"


class User(Base):
    """Account belonging to a workspace."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", create_type=False),
        nullable=False,
        default=UserRole.operator,
    )
    job_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mfa_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    preferences: Mapped[dict | None] = mapped_column(MutableJSONB, nullable=True)
    notification_settings: Mapped[dict | None] = mapped_column(MutableJSONB, nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # False until a fresh (e.g. OAuth-provisioned) user completes their profile;
    # gates the app shell for accounts that cannot password-login.
    profile_completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_site_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deactivated_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )

    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="users")  # type: ignore[name-defined,unused-ignore]
    sessions: Mapped[list["Session"]] = relationship(  # type: ignore[name-defined,unused-ignore]
        "Session", back_populates="user", cascade="all, delete-orphan"
    )


class UserIdentity(Base):
    """Linked OAuth identity (e.g. Google)."""

    __tablename__ = "user_identities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_uid: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )


class MfaRecoveryCode(Base):
    """Hashed TOTP recovery code."""

    __tablename__ = "mfa_recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    used_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Session(Base):
    """Refresh-token session."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    refresh_hash: Mapped[str] = mapped_column(Text, nullable=False)
    device: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Where the sign-in came from, as recorded by the auth router: the client's
    # address. (It was named ``ip_country`` while never holding a country code —
    # see migration 013.)
    origin: Mapped[str | None] = mapped_column(Text, nullable=True)
    trusted_device: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship("User", back_populates="sessions")


class UserPreference(Base):
    """Per-user UI preferences — persisted as row for queryability.

    Primary store is ``users.preferences`` JSONB; this table mirrors it for
    deployments that prefer normalized columns. ``get_or_create`` helpers
    check both locations.
    """

    __tablename__ = "user_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Dark is the platform default; a user who picks light keeps it.
    theme: Mapped[str] = mapped_column(Text, nullable=False, default="dark")
    language: Mapped[str] = mapped_column(Text, nullable=False, default="en")
    density: Mapped[str] = mapped_column(Text, nullable=False, default="comfortable")
    default_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("locations.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )


class UserNotificationSetting(Base):
    """Per-user notification channel toggles."""

    __tablename__ = "user_notification_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    email_digest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    push_alerts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    weekly_report: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    in_app_alerts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.UTC),
    )
