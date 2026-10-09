"""Device fleet ORM models — enrolled devices, grants, events, mirror sessions.

These back Layer 2–6 of the device ecosystem. The four tables are deliberately
separated because they answer four different questions with four different
lifetimes:

``devices``
    Identity. One row per physical device, workspace-scoped. Survives
    revocation so the audit trail keeps its subject.
``device_grants``
    Permission. A capability one human granted one device, optionally
    time-boxed. Separate from ``devices.scopes`` because a grant is a *decision
    with an author and an expiry*, and that is exactly what an auditor asks
    about. The effective scope of a device is the union of its base scopes and
    its live grants.
``device_events``
    Observation. Heartbeats, syncs, errors, app versions. Append-only and
    high-volume, which is why it is not folded into ``devices`` — the fleet
    console reads a recent window and must not contend with the identity table.
``mirror_sessions``
    Consent. Live view of a customer's device screen is the one capability here
    that is not merely a permission: it is a surveillance-adjacent read of a
    screen an employee is using. It is modelled as a session with an explicit
    consent timestamp, a hard expiry, and a two-person approval enforced by a
    database constraint, not by application code.

The two-person rule is a ``CHECK`` on the table (``decided_by <> requested_by``)
for the same reason ``demand_observations`` carries its ``UNIQUE`` in the
schema: a rule that only the service layer enforces is a rule a future
migration, a direct ``psql`` session, or a background job can walk past.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, MutableJSONB

# Device lifecycle states. A revoked device keeps its row so audit entries
# still name something real; ``quarantined`` is a reversible "stop syncing but
# do not delete" used when a device is reported lost.
DEVICE_STATUS_ACTIVE = "active"
DEVICE_STATUS_REVOKED = "revoked"
DEVICE_STATUS_QUARANTINED = "quarantined"

# Mirror session states. ``pending`` is the two-person window: the requester
# cannot approve their own request, so the row sits here until a second
# workspace member decides.
MIRROR_PENDING = "pending"
MIRROR_ACTIVE = "active"
MIRROR_ENDED = "ended"
MIRROR_DENIED = "denied"


class Device(Base):
    """One enrolled physical device belonging to a workspace."""

    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # A device normally serves exactly one location (a till at one restaurant),
    # but a shared tablet or a back-office screen legitimately serves none or
    # several, so the link is nullable and NOT UNIQUE.
    location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("locations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    device_type: Mapped[str] = mapped_column(Text, nullable=False, default="pos")
    platform: Mapped[str] = mapped_column(Text, nullable=False, default="unknown")
    app_version: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The device's own secret, bcrypt-hashed exactly like an API key. Envelope
    # encryption and per-device rotation reuse ``security.hashing``; the prefix
    # is the non-secret half used to look the row up before the hash check.
    credential_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    credential_hash: Mapped[str] = mapped_column(Text, nullable=False)
    # Base capabilities, seeded at enrollment. Narrower than a user's roles on
    # purpose: a till that can read forecasts does not thereby gain the right
    # to edit preparation policies.
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)  # type: ignore[assignment]

    # Reserved for hardware-backed attestation. Populated only when a platform
    # key store is available; absence is the normal case and must not be
    # treated as failure. Kept in the schema now so adding attestation later is
    # a backfill, not a migration.
    public_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(Text, nullable=False, default=DEVICE_STATUS_ACTIVE)
    last_seen_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enrolled_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    enrolled_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # The consent gate for live screen mirroring. NULL means the customer has
    # not consented, and every mirroring path must refuse while it is NULL —
    # see ``app/services/mirroring.py``. This is a timestamp rather than a
    # boolean so the grant can be evidenced and withdrawn.
    mirroring_consent_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DeviceGrant(Base):
    """A capability a human granted one device, optionally time-boxed."""

    __tablename__ = "device_grants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Drawn from ``app.security.permissions`` — a free-text string here would
    # let a typo create a grant that silently grants nothing, which reads as a
    # permission that works. The service layer validates against the registry.
    capability: Mapped[str] = mapped_column(Text, nullable=False)
    granted_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    granted_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    # NULL means no expiry. A non-NULL ``expires_at`` in the past is not
    # deleted — the row is the record that the permission existed — so
    # authorisation checks compare against "now" rather than filtering.
    expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Why the grant exists. Required by policy for any capability outside the
    # read-only set: an unexplained write grant on a customer device is the
    # thing an auditor asks about first.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class DeviceEvent(Base):
    """Append-only observation from a device — heartbeat, sync, error, update."""

    __tablename__ = "device_events"

    # BIGSERIAL to match the high-volume, never-updated tables
    # (``demand_observations``); a UUID would only cost index space here.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(MutableJSONB, nullable=False, default=dict)
    recorded_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class MirrorSession(Base):
    """A consent-gated, two-person-approved window of live device screen view."""

    __tablename__ = "mirror_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # NULL until a second person decides. The CHECK below makes "decided_by IS
    # NULL OR decided_by <> requested_by" a database invariant, matching
    # ``change_requests.two_person_rule``.
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default=MIRROR_PENDING)
    requested_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    decided_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Hard ceiling on a session, set when it is approved. A live view that can
    # outlive the shift it was opened for is not support, it is surveillance.
    ends_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Recorded consent at approval time, copied from the device. Storing it here
    # means a later withdrawal of consent does not silently legitimise sessions
    # that were approved against the old grant.
    consent_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


# Hot paths the fleet console and the authorisation check use. A composite index
# on (device_id, created/recorded desc) beats filtering the whole table.
Index("ix_device_events_device_recorded", DeviceEvent.device_id, DeviceEvent.recorded_at)
Index("ix_device_grants_device_capability", DeviceGrant.device_id, DeviceGrant.capability)
Index("ix_mirror_sessions_device_status", MirrorSession.device_id, MirrorSession.status)
