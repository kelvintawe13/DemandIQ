"""Device grant resolution — what a device is actually allowed to do, right now.

A device's permissions are assembled from more than one place, and the assembly
is the whole security property of the fleet layer. Three inputs bear on it:

1. ``devices.scopes`` — the base list seeded at enrollment, which the device
   router restricts to :data:`DEVICE_SCOPES` (``device:sync`` and nothing else);
2. the device's ``device_grants`` rows that are neither revoked nor expired;
3. the location the device is bound to, which may impose a ceiling.

**A grant widens, deliberately.** It is not a restriction on top of the base
list. The reasoning:

* If a grant could only narrow, it could narrow *nothing*. The only scope
  enrollment will accept is ``device:sync``, so a narrowing grant would be a
  no-op against every device the fleet can create — the table, the audit trail
  and this whole layer would be unreachable code. Widening is the entire
  purpose of a grant row.
* ``app/models/device.py`` states the rule in as many words: the effective
  scope of a device is the union of its base scopes and its live grants. A
  grant is a *decision with an author and an expiry*, which is why it is a
  table rather than a string in ``devices.scopes``.
* The hazard a union invites — a till that holds ``device:sync`` silently
  acquiring an operator capability — is not actually caused by the union. It is
  caused by reading "it holds some capability" as "it may do this one". That is
  a bug in a *call site*, and it is closed here by three things: the union is
  computed in exactly one function, both inputs are validated against the
  capability registry before they are ever stored, and
  :func:`effective_scopes_report` publishes which source every effective
  capability came from, so a widened capability is always traceable to a grant
  row with a named human and, outside the read-only set, a written reason.

So: widening is allowed and visible; narrowing is not the grant's job; and the
location ceiling is intersected *after* the union, because it is the one input
that is genuinely a constraint on the device rather than a source of rights.

Expiry is a half-open window
----------------------------
A grant is live while ``expires_at IS NULL OR now < expires_at``. The instant
named by ``expires_at`` is the first instant at which the permission no longer
holds, so ``expires_at == now`` is expired — a grant written for "one hour" is
valid for ``[granted_at, granted_at + 1h)`` and not a microsecond longer. The
same rule is what makes the SQL form ``now < expires_at`` a direct translation
of the Python one, which is worth having in a check that is on the hot path of
every device call.

Nothing here deletes an expired grant. The row is the evidence that a
permission existed and who asked for it, so liveness is *computed* by comparing
against ``now`` — the comparison lives in :func:`grant_state` and nowhere else.

Cross-workspace
---------------
Every query in this module is scoped to ``workspace_id``, derived from the
``Device`` the caller was already authorised for. A grant belonging to another
tenant is not found, not refused: a 403 would confirm that the row exists, and
the fleet is the one list an attacker would like to enumerate.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.device import DEVICE_STATUS_REVOKED, Device, DeviceGrant
from app.models.location import Location
from app.security.permissions import (
    ACCESS_READ,
    AGENT_CONFIGURE,
    ALERTS_CONFIGURE,
    ALERTS_READ,
    ALERTS_WRITE,
    ANALYTICS_READ,
    API_KEYS_MANAGE,
    APPROVALS_MANAGE,
    AUDIT_READ,
    BILLING_MANAGE,
    BILLING_READ,
    CONNECTORS_MANAGE,
    DATASETS_MANAGE,
    DEVICE_GRANTS_MANAGE,
    DEVICE_MIRROR_APPROVE,
    DEVICE_MIRROR_REQUEST,
    DEVICE_SYNC,
    DEVICES_MANAGE,
    DEVICES_READ,
    EXPERIMENTS_MANAGE,
    FEATURES_MANAGE,
    FORECASTS_READ,
    INGEST_WRITE,
    LOCATIONS_READ,
    LOCATIONS_WRITE,
    MEMBERS_MANAGE,
    MODELS_MANAGE,
    MODELS_READ,
    PIPELINE_READ,
    PIPELINE_RUN,
    POLICIES_READ,
    POLICIES_SIMULATE,
    POLICIES_WRITE,
    QUALITY_CONFIGURE,
    QUALITY_FIX,
    QUALITY_READ,
    REPORTS_GENERATE,
    REPORTS_READ,
    SOURCES_READ,
    SOURCES_WRITE,
    WEBHOOKS_MANAGE,
    WORKSPACE_MANAGE,
    WORKSPACE_READ,
)

logger = logging.getLogger(__name__)

__all__ = [
    "GRANT_ACTIVE",
    "GRANT_EXPIRED",
    "GRANT_REVOKED",
    "READ_ONLY_CAPABILITIES",
    "REGISTERED_CAPABILITIES",
    "DeviceGrantError",
    "GrantView",
    "WithheldCapability",
    "describe_grants",
    "device_may",
    "effective_scopes",
    "effective_scopes_report",
    "grant_capability",
    "grant_state",
    "list_grants",
    "revoke_grant",
    "withheld_capabilities",
]

#: Every capability a grant may name. Deliberately the *whole* registry rather
#: than ``DEVICE_SCOPES``: ``DEVICE_SCOPES`` is what enrollment seeds, and a
#: grant whose only permitted value were the enrollment default could not widen
#: anything. Listed name by name rather than derived by pattern-matching the
#: permissions module, because a registry built by introspection is a registry
#: that silently loses a capability the day somebody renames one — and a missing
#: capability here reads as a permission that cannot be granted, which is the
#: same silence a typo would produce.
REGISTERED_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        ACCESS_READ,
        AGENT_CONFIGURE,
        ALERTS_CONFIGURE,
        ALERTS_READ,
        ALERTS_WRITE,
        ANALYTICS_READ,
        API_KEYS_MANAGE,
        APPROVALS_MANAGE,
        AUDIT_READ,
        BILLING_MANAGE,
        BILLING_READ,
        CONNECTORS_MANAGE,
        DATASETS_MANAGE,
        DEVICE_GRANTS_MANAGE,
        DEVICE_MIRROR_APPROVE,
        DEVICE_MIRROR_REQUEST,
        DEVICE_SYNC,
        DEVICES_MANAGE,
        DEVICES_READ,
        EXPERIMENTS_MANAGE,
        FEATURES_MANAGE,
        FORECASTS_READ,
        INGEST_WRITE,
        LOCATIONS_READ,
        LOCATIONS_WRITE,
        MEMBERS_MANAGE,
        MODELS_MANAGE,
        MODELS_READ,
        PIPELINE_READ,
        PIPELINE_RUN,
        POLICIES_READ,
        POLICIES_SIMULATE,
        POLICIES_WRITE,
        QUALITY_CONFIGURE,
        QUALITY_FIX,
        QUALITY_READ,
        REPORTS_GENERATE,
        REPORTS_READ,
        SOURCES_READ,
        SOURCES_WRITE,
        WEBHOOKS_MANAGE,
        WORKSPACE_MANAGE,
        WORKSPACE_READ,
    }
)

#: The capabilities that need no justification. Derived from the registry by the
#: ``resource:action`` convention ``app/security/permissions.py`` already
#: documents: ``:read`` is the read-only verb, everything else is a decision or a
#: mutation. Derived rather than listed so a new ``*:read`` capability joins the
#: set by being one, and so the policy in ``app/models/device.py`` — a reason is
#: required for anything outside the read-only set — cannot drift from the naming
#: scheme the rest of the system relies on.
READ_ONLY_CAPABILITIES: Final[frozenset[str]] = frozenset(
    capability for capability in REGISTERED_CAPABILITIES if capability.endswith(":read")
)

#: A grant's liveness, as resolved against a moment.
GRANT_ACTIVE: Final = "active"
GRANT_EXPIRED: Final = "expired"
GRANT_REVOKED: Final = "revoked"

#: The location table carries no scope column today, so the ceiling is read
#: through whichever of these names the location-permission layer lands on.
#: Absence of both means "unbounded" — see :func:`_location_ceiling`.
_LOCATION_SCOPE_COLUMNS: Final[tuple[str, ...]] = ("device_scopes", "scopes")


class DeviceGrantError(ValueError):
    """Domain error for a refused grant write.

    Carries the HTTP status and the error code so the router does not have to
    re-derive either from the message, and so a rule enforced here cannot be
    reported differently by two different callers.
    """

    def __init__(
        self, code: str, message: str, *, status_code: int, field: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.field = field


@dataclass(frozen=True)
class GrantView:
    """One grant as the API publishes it, with its liveness already resolved.

    Keeping the annotation next to the row rather than in the router means the
    console, a future export, and this module's own tests all read liveness from
    one implementation.
    """

    id: uuid.UUID
    device_id: uuid.UUID
    capability: str
    granted_by: uuid.UUID
    granted_at: datetime.datetime | None
    expires_at: datetime.datetime | None
    revoked_at: datetime.datetime | None
    revoked_by: uuid.UUID | None
    reason: str | None
    state: str
    effective: bool
    detail: str

    @classmethod
    def of(cls, grant: DeviceGrant, *, now: datetime.datetime) -> GrantView:
        state = grant_state(grant, now=now)
        return cls(
            id=grant.id,
            device_id=grant.device_id,
            capability=grant.capability,
            granted_by=grant.granted_by,
            granted_at=grant.granted_at,
            expires_at=grant.expires_at,
            revoked_at=grant.revoked_at,
            revoked_by=grant.revoked_by,
            reason=grant.reason,
            state=state,
            effective=state == GRANT_ACTIVE,
            detail=_state_detail(grant, now=now),
        )


@dataclass(frozen=True)
class WithheldCapability:
    """A capability the device does not hold, and the closest thing to a reason."""

    capability: str
    detail: str


def _utc(value: datetime.datetime | None) -> datetime.datetime | None:
    """Coerce a stored timestamp to an aware UTC datetime, or ``None``.

    A ``timestamptz`` column always comes back aware, so a naive value means the
    row was written to something else. Comparing an aware ``now`` against a naive
    timestamp raises ``TypeError`` and would turn a routine permission check into
    a 500; reading a naive stamp as UTC is the least-wrong of the alternatives.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value


def _iso(value: datetime.datetime | None) -> str:
    moment = _utc(value)
    return moment.isoformat() if moment is not None else "unknown"


def grant_state(grant: DeviceGrant, *, now: datetime.datetime) -> str:
    """Resolve a grant to ``active``/``expired``/``revoked`` against ``now``.

    Revocation outranks expiry when both apply. A deliberate withdrawal is the
    fact an investigator is looking for; the clock lapsing on a grant somebody
    already took back is a detail, and reporting "expired" there would describe
    the wrong event.

    The expiry test is ``now < expires_at`` — the moment named by ``expires_at``
    is the first moment the permission no longer holds. See the module docstring.
    """
    if grant.revoked_at is not None:
        return GRANT_REVOKED
    expires_at = _utc(grant.expires_at)
    if expires_at is not None and now >= expires_at:
        return GRANT_EXPIRED
    return GRANT_ACTIVE


def _state_detail(grant: DeviceGrant, *, now: datetime.datetime) -> str:
    """One sentence saying why a grant is in the state it is in."""
    state = grant_state(grant, now=now)
    if state == GRANT_REVOKED:
        who = f" by {grant.revoked_by}" if grant.revoked_by is not None else ""
        return f"revoked at {_iso(grant.revoked_at)}{who}"
    if state == GRANT_EXPIRED:
        return f"expired at {_iso(grant.expires_at)}"
    if grant.expires_at is None:
        return f"in effect since {_iso(grant.granted_at)} with no expiry"
    return f"in effect until {_iso(grant.expires_at)}"


def list_grants(db: Session, device: Device) -> list[DeviceGrant]:
    """Every grant row for one device, newest first, inside its workspace.

    Expired and revoked rows are included on purpose — this is the audit read,
    and an operator asking "was this ever allowed?" is answered by a row that
    says yes and when it stopped. Liveness is the caller's business, computed by
    :func:`grant_state`, and the query deliberately carries no expiry predicate:
    a filter that dropped the lapsed rows is a filter that would also make them
    impossible to find again.
    """
    stmt = (
        select(DeviceGrant)
        .where(
            DeviceGrant.workspace_id == device.workspace_id,
            DeviceGrant.device_id == device.id,
        )
        .order_by(DeviceGrant.granted_at.desc())
    )
    return list(db.scalars(stmt).all())


def describe_grants(
    db: Session, device: Device, *, now: datetime.datetime | None = None
) -> list[GrantView]:
    """:func:`list_grants` with each row's liveness resolved."""
    moment = _utc(now) or datetime.datetime.now(datetime.UTC)
    return [GrantView.of(grant, now=moment) for grant in list_grants(db, device)]


def _location_ceiling(db: Session, device: Device) -> frozenset[str] | None:
    """What the device's bound location permits, or ``None`` for no ceiling.

    The location table has no capability column yet, so the ceiling is probed
    through :data:`_LOCATION_SCOPE_COLUMNS` and stays inert until one exists.
    That is why the intersection below is conditional rather than unconditional:
    a location that declares nothing is asserting "no limit", not "no
    capabilities", and reading it the other way would leave every bound device
    with an empty scope set.

    The lookup is deliberately not re-checked against the location's workspace.
    A device bound to another tenant's location is bad data, and the safe
    direction for a ceiling is to fail closed.
    """
    if device.location_id is None:
        return None
    location = db.get(Location, device.location_id)
    if location is None:
        return None
    for column in _LOCATION_SCOPE_COLUMNS:
        declared: object = getattr(location, column, None)
        if isinstance(declared, (list, tuple, set, frozenset)):
            return frozenset(str(capability) for capability in declared)
    return None


def effective_scopes(
    db: Session, device: Device, *, now: datetime.datetime | None = None
) -> frozenset[str]:
    """What ``device`` may do right now — the one answer to "what is allowed?".

    The union of its base scopes and its live grants, intersected with the
    ceiling its bound location imposes. Device *status* is not folded in: a
    revoked device is refused at authentication (``app/security/device_auth.py``)
    with a status of its own, and collapsing that into "no scopes" would leave a
    support engineer with an empty set and no explanation.

    ``now`` is injectable so the expiry boundary can be tested exactly rather
    than approximately, and so a caller that already has a resolved moment reads
    one consistent answer rather than two that a grant expiring between them
    could make disagree.
    """
    moment = _utc(now) or datetime.datetime.now(datetime.UTC)
    scopes = {str(scope) for scope in (device.scopes or [])}
    for grant in list_grants(db, device):
        if grant_state(grant, now=moment) == GRANT_ACTIVE:
            scopes.add(grant.capability)
    ceiling = _location_ceiling(db, device)
    if ceiling is not None:
        scopes &= ceiling
    return frozenset(scopes)


def withheld_capabilities(
    views: Sequence[GrantView], effective: frozenset[str]
) -> list[WithheldCapability]:
    """Registry capabilities the device does not hold, with the nearest reason.

    This is what turns "the till can't do X" from a support ticket into an
    answer. A capability that was never granted and one whose grant lapsed on
    the first of the month are the same refusal at the call site and a different
    conversation with the customer, and the operator can only have the second
    one if the first one is distinguished.
    """
    latest: dict[str, GrantView] = {}
    for view in views:
        if view.capability not in latest:
            latest[view.capability] = view
    out: list[WithheldCapability] = []
    for capability in sorted(REGISTERED_CAPABILITIES - effective):
        latest_row = latest.get(capability)
        if latest_row is None:
            detail = "never granted and not among the device's base scopes"
        else:
            # The view's own sentence already distinguishes the two ways a grant
            # stops working, so it is quoted rather than reworded.
            detail = latest_row.detail
        out.append(WithheldCapability(capability=capability, detail=detail))
    return out


def _validate_capability(capability: object) -> str:
    """Refuse anything the registry does not contain, rather than storing it.

    A grant whose capability matches nothing is indistinguishable from a
    permission that works: the console lists it, the database accepts it, and the
    device is refused at the route. Refusing at write time is the only point
    where the operator is still in front of the form.
    """
    if not isinstance(capability, str) or capability not in REGISTERED_CAPABILITIES:
        raise DeviceGrantError(
            "unknown_capability",
            "capability must be one of the registered capabilities: "
            f"{', '.join(sorted(REGISTERED_CAPABILITIES))}",
            status_code=400,
            field="capability",
        )
    return capability


def _validate_reason(capability: str, reason: object) -> str | None:
    """Demand a written justification for anything outside the read-only set.

    The policy is ``app/models/device.py``'s: an unexplained grant on a customer
    device is the first thing an auditor asks about. Blank is treated as absent,
    because a form that submits ``" "`` has not been filled in.
    """
    if isinstance(reason, str) and reason.strip():
        return reason.strip()
    if capability in READ_ONLY_CAPABILITIES:
        return None
    raise DeviceGrantError(
        "reason_required",
        f"reason is required for a '{capability}' grant; a non-read-only "
        "capability on a customer device must be explainable",
        status_code=400,
        field="reason",
    )


def _validate_expiry(
    expires_at: datetime.datetime | None, *, now: datetime.datetime
) -> datetime.datetime | None:
    """Refuse a naive or already-elapsed expiry.

    A naive timestamp would be compared against an aware ``now`` and raise rather
    than decide. An expiry at or before now is a grant that is dead on arrival:
    storing it would create a row that reads like a granted permission and
    never once was.
    """
    if expires_at is None:
        return None
    if expires_at.tzinfo is None:
        raise DeviceGrantError(
            "expiry_requires_timezone",
            "expires_at must carry a UTC offset",
            status_code=400,
            field="expires_at",
        )
    if expires_at <= now:
        raise DeviceGrantError(
            "expiry_in_past",
            "expires_at must be in the future; a grant that has already lapsed "
            "is evidence of nothing",
            status_code=400,
            field="expires_at",
        )
    return expires_at


def grant_capability(
    db: Session,
    *,
    device: Device,
    capability: str,
    granted_by: uuid.UUID,
    reason: str | None = None,
    expires_at: datetime.datetime | None = None,
    now: datetime.datetime | None = None,
) -> GrantView:
    """Grant one capability to one device, on one human's authority.

    The device argument carries the workspace, so the row can only ever be
    written inside a tenant the caller was already authorised for; there is no
    ``workspace_id`` parameter that could disagree with the device it is given.

    Two shapes are refused rather than stored, both because they produce a
    permission whose behaviour differs from what the row says it does:

    * a capability the device already holds in ``devices.scopes`` — revoking it
      later would appear to remove the capability and would not, because the base
      scope underneath is untouched;
    * a second live grant for a capability it already has by grant — which of
      the two rows is live is exactly the question an auditor would then have to
      answer from the timestamps.
    """
    moment = _utc(now) or datetime.datetime.now(datetime.UTC)
    wanted = _validate_capability(capability)
    justification = _validate_reason(wanted, reason)
    expiry = _validate_expiry(expires_at, now=moment)

    if device.status == DEVICE_STATUS_REVOKED:
        # A revoked device is re-enrolled, not revived (see
        # ``app/routers/devices.py``); a grant cannot outlive that.
        raise DeviceGrantError(
            "device_revoked",
            "A revoked device cannot be granted capabilities",
            status_code=409,
        )
    if wanted in (device.scopes or []):
        raise DeviceGrantError(
            "already_a_base_scope",
            f"'{wanted}' is already one of this device's base scopes; it is "
            "always in effect and cannot be time-boxed or revoked here",
            status_code=409,
            field="capability",
        )
    for existing in list_grants(db, device):
        if existing.capability == wanted and grant_state(existing, now=moment) == GRANT_ACTIVE:
            raise DeviceGrantError(
                "already_granted",
                f"'{wanted}' is already granted to this device and has not "
                "expired. Revoke the existing grant first",
                status_code=409,
                field="capability",
            )

    row = DeviceGrant(
        id=uuid.uuid4(),
        workspace_id=device.workspace_id,
        device_id=device.id,
        capability=wanted,
        granted_by=granted_by,
        granted_at=moment,
        expires_at=expiry,
        revoked_at=None,
        revoked_by=None,
        reason=justification,
    )
    db.add(row)
    db.flush()
    logger.info(
        "Device grant created device=%s workspace=%s capability=%s by=%s expires_at=%s",
        device.id,
        device.workspace_id,
        wanted,
        granted_by,
        _iso(expiry),
    )
    return GrantView.of(row, now=moment)


def revoke_grant(
    db: Session,
    *,
    device: Device,
    grant_id: uuid.UUID,
    revoked_by: uuid.UUID,
    now: datetime.datetime | None = None,
) -> GrantView:
    """Revoke a grant, keeping the row and the first revocation timestamp.

    Idempotent, like device revocation: a second click must not move
    ``revoked_at``, because that timestamp is the evidence of *when* the
    permission was taken back. The row is never deleted either — a device that
    held a capability and no longer does is a fact the audit trail is made of.
    """
    moment = _utc(now) or datetime.datetime.now(datetime.UTC)
    row = _find_grant(db, device, grant_id)
    if row.revoked_at is None:
        row.revoked_at = moment
        row.revoked_by = revoked_by
        db.flush()
        logger.info(
            "Device grant revoked grant=%s device=%s workspace=%s by=%s",
            row.id,
            device.id,
            device.workspace_id,
            revoked_by,
        )
    return GrantView.of(row, now=moment)


def _find_grant(db: Session, device: Device, grant_id: uuid.UUID) -> DeviceGrant:
    """Fetch a grant that belongs to this device in this workspace, or raise 404.

    A grant id from another tenant, or one belonging to a different device in
    this tenant, is simply not found. Naming the difference would confirm that
    the row exists somewhere.
    """
    stmt = select(DeviceGrant).where(
        DeviceGrant.workspace_id == device.workspace_id,
        DeviceGrant.device_id == device.id,
        DeviceGrant.id == grant_id,
    )
    row = db.scalars(stmt).first()
    if row is None:
        raise DeviceGrantError("not_found", "Device grant not found", status_code=404, field=None)
    return row


def effective_scopes_report(
    db: Session, device: Device, *, now: datetime.datetime | None = None
) -> dict[str, object]:
    """Everything needed to answer "why can this device not do X?", in one read.

    Each effective capability is attributed to the source that supplied it, and
    every capability the device lacks is listed with the nearest reason. Assembled
    from :func:`effective_scopes` rather than by re-deriving the union, so the
    report and the check a route performs cannot disagree.
    """
    moment = _utc(now) or datetime.datetime.now(datetime.UTC)
    views = describe_grants(db, device, now=moment)
    effective = effective_scopes(db, device, now=moment)
    live = [view for view in views if view.effective]
    return {
        "device_id": str(device.id),
        "workspace_id": str(device.workspace_id),
        "base_scopes": sorted(str(scope) for scope in (device.scopes or [])),
        "granted": [
            {
                "capability": view.capability,
                "grant_id": str(view.id),
                "granted_by": str(view.granted_by),
                "granted_at": _iso(view.granted_at),
                "expires_at": _iso(view.expires_at) if view.expires_at else None,
                "reason": view.reason,
            }
            for view in live
        ],
        "effective": sorted(effective),
        "withheld": [
            {"capability": item.capability, "detail": item.detail}
            for item in withheld_capabilities(views, effective)
        ],
    }


def device_may(
    db: Session, device: Device, capability: str, *, now: datetime.datetime | None = None
) -> bool:
    """Whether ``device`` may exercise ``capability`` right now.

    Exists so that a device-facing route asks one question of one function
    instead of reaching for ``DevicePrincipal.scopes`` — which is the base list
    by design — and silently ignoring every grant on the device.
    """
    return capability in effective_scopes(db, device, now=now)
