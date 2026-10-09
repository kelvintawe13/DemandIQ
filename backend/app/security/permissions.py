"""Role capability model — the single source of truth for who may do what.

Roles are *named bundles of capabilities*. Routes check capabilities, never
role names, so a role can be re-scoped (or a new one added) without hunting for
``role in (...)`` comparisons scattered across routers. ``/auth/me`` and
``GET /roles`` are generated from this module, so the client and the access
matrix cannot drift from what the API actually enforces.

The five bundles reflect who owns which part of the work:

``viewer``
    Reads workspace output and changes nothing.
``operator`` (shown as **Data Clerk**)
    Gets demand data in and clean: ingestion, sources, quality fixes, alerts,
    plus read access to forecasts/analytics so they can see what their data
    drives.
``analyst``
    The clerk's reads plus analysis, policy simulation and report generation.
``administrator``
    The platform/ML engineer: trains and promotes models, manages datasets,
    features, experiments, connectors, pipeline and access.
``owner``
    The business: membership, roles, billing, audit and oversight of everything
    the workspace produces. Deliberately **without** the modelling console —
    owners do not train or promote models.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.models.user import User, UserRole

# --------------------------------------------------------------------------- #
# Capability names. ``resource:action``, matching the vocabulary the API
# already used in permission payloads.
# --------------------------------------------------------------------------- #
WORKSPACE_READ = "workspace:read"
WORKSPACE_MANAGE = "workspace:manage"
LOCATIONS_READ = "locations:read"
LOCATIONS_WRITE = "locations:write"
FORECASTS_READ = "forecasts:read"
ANALYTICS_READ = "analytics:read"
REPORTS_READ = "reports:read"
REPORTS_GENERATE = "reports:generate"
INGEST_WRITE = "ingest:write"
SOURCES_READ = "sources:read"
SOURCES_WRITE = "sources:write"
QUALITY_READ = "quality:read"
QUALITY_FIX = "quality:fix"
QUALITY_CONFIGURE = "quality:configure"
ALERTS_READ = "alerts:read"
ALERTS_WRITE = "alerts:write"
ALERTS_CONFIGURE = "alerts:configure"
POLICIES_READ = "policies:read"
POLICIES_SIMULATE = "policies:simulate"
POLICIES_WRITE = "policies:write"
MODELS_READ = "models:read"
MODELS_MANAGE = "models:manage"
DATASETS_MANAGE = "datasets:manage"
EXPERIMENTS_MANAGE = "experiments:manage"
FEATURES_MANAGE = "features:manage"
PIPELINE_READ = "pipeline:read"
PIPELINE_RUN = "pipeline:run"
CONNECTORS_MANAGE = "connectors:manage"
WEBHOOKS_MANAGE = "webhooks:manage"
API_KEYS_MANAGE = "api_keys:manage"
AUDIT_READ = "audit:read"
APPROVALS_MANAGE = "approvals:manage"
AGENT_CONFIGURE = "agent:configure"
MEMBERS_MANAGE = "members:manage"
ACCESS_READ = "access:read"
BILLING_READ = "billing:read"
BILLING_MANAGE = "billing:manage"

# --- Device fleet --------------------------------------------------------- #
#
# A separate namespace from the user capabilities above, because a device is
# not a user and its rights are derived differently. A user's capabilities come
# from their role. A device's come from three things that are ANDed, never
# ORed: its base ``devices.scopes`` (seeded at enrollment), its live
# ``device_grants`` rows, and the location it is bound to. Keeping them in one
# flat string space would invite code that treats "has capability X" as
# sufficient — which is the bug that would let a till with
# ``forecasts:read`` also edit preparation policies.
#
# DEVICE_SYNC is the only capability a device normally needs. Everything else
# in this group is an operator action *about* devices, not a permission the
# device itself exercises.
DEVICE_SYNC = "device:sync"
DEVICES_READ = "devices:read"
DEVICES_MANAGE = "devices:manage"
DEVICE_GRANTS_MANAGE = "device_grants:manage"
# Consent-gated live screen view. Split from DEVICES_MANAGE deliberately:
# managing the fleet (enrol, rename, revoke) is routine administration, while
# granting the ability to watch a customer's screen mid-shift is not, and the
# two must be grantable separately.
DEVICE_MIRROR_REQUEST = "device_mirror:request"
DEVICE_MIRROR_APPROVE = "device_mirror:approve"

#: Capabilities a *device* may hold on its own row. A value outside this set
#: on ``devices.scopes`` is a configuration error, not a wider grant — the
#: device-facing scopes and the operator-facing ones are different lists on
#: purpose.
DEVICE_SCOPES: frozenset[str] = frozenset({DEVICE_SYNC})

# Capabilities the modelling console is built on: the owner is deliberately
# excluded from all of them (see ``_OWNER``).
MODELLING_CAPABILITIES = frozenset(
    {MODELS_READ, MODELS_MANAGE, DATASETS_MANAGE, EXPERIMENTS_MANAGE, FEATURES_MANAGE}
)

# --------------------------------------------------------------------------- #
# Bundles
# --------------------------------------------------------------------------- #

# Reading the workspace's own output: everyone, including viewers. Every seat
# may see the tenant it works in (name, locale, footprint) — that is not a
# privilege, it is context for the screen they are already on.
_READS = frozenset({WORKSPACE_READ, LOCATIONS_READ, FORECASTS_READ, ANALYTICS_READ})

_VIEWER = _READS

# Data Clerk.
_CLERK = _READS | frozenset(
    {
        REPORTS_READ,
        INGEST_WRITE,
        SOURCES_READ,
        SOURCES_WRITE,
        QUALITY_READ,
        QUALITY_FIX,
        ALERTS_READ,
        ALERTS_WRITE,
        POLICIES_READ,
    }
)

_ANALYST = _CLERK | frozenset(
    {
        REPORTS_GENERATE,
        POLICIES_SIMULATE,
        MODELS_READ,
    }
)

# Administrator: the modelling + platform console.
_ADMIN = _ANALYST | frozenset(
    {
        WORKSPACE_MANAGE,
        LOCATIONS_WRITE,
        ALERTS_CONFIGURE,
        QUALITY_CONFIGURE,
        POLICIES_WRITE,
        MODELS_MANAGE,
        DATASETS_MANAGE,
        EXPERIMENTS_MANAGE,
        FEATURES_MANAGE,
        PIPELINE_READ,
        PIPELINE_RUN,
        CONNECTORS_MANAGE,
        WEBHOOKS_MANAGE,
        API_KEYS_MANAGE,
        AGENT_CONFIGURE,
        AUDIT_READ,
        APPROVALS_MANAGE,
        MEMBERS_MANAGE,
        ACCESS_READ,
        BILLING_READ,
        # Fleet administration, but NOT the mirroring capabilities. Those are
        # deliberately withheld from the administrator role and reachable only
        # through the owner, because the capability to watch a customer's
        # screen is a governance decision rather than a platform one. An
        # administrator who needs it has to be made an owner.
        DEVICES_READ,
        DEVICES_MANAGE,
        DEVICE_GRANTS_MANAGE,
        DEVICE_MIRROR_REQUEST,
    }
)

# Owner: the administrator's oversight and governance reach, minus every part
# of the modelling and integration console and minus pipeline execution — plus
# the two edges the administrator must not hold on their own: the commercial
# side (billing) they may only read, and approval of consent-gated remote
# viewing. An administrator may request mirroring but never approve it, so
# every approved session has a second, higher-authority pair of eyes on it.
_OWNER = _ADMIN - (
    MODELLING_CAPABILITIES
    | frozenset(
        {
            CONNECTORS_MANAGE,
            WEBHOOKS_MANAGE,
            API_KEYS_MANAGE,
            AGENT_CONFIGURE,
            PIPELINE_RUN,
        }
    )
) | frozenset({BILLING_MANAGE, DEVICE_MIRROR_APPROVE})

ROLE_CAPABILITIES: dict[UserRole, frozenset[str]] = {
    UserRole.viewer: _VIEWER,
    UserRole.operator: _CLERK,
    UserRole.analyst: _ANALYST,
    UserRole.administrator: _ADMIN,
    UserRole.owner: _OWNER,
}

# --------------------------------------------------------------------------- #
# Hierarchy. Capabilities answer "may this actor do X?"; the hierarchy answers
# "may this actor act *on* that account?" — the question member management
# asks. It is what stops an administrator from minting an owner (or promoting
# themselves), i.e. from granting a bundle wider than their own.
#
# The ranks are ordered by *reach over people*, not by raw capability count: an
# administrator holds more capabilities than an owner (the whole modelling
# console) but governs fewer accounts.
# --------------------------------------------------------------------------- #
ROLE_RANK: dict[UserRole, int] = {
    UserRole.viewer: 0,
    UserRole.operator: 1,
    UserRole.analyst: 2,
    UserRole.administrator: 3,
    UserRole.owner: 4,
}

# The highest rank anyone may be granted through the API. Ownership is not
# invitable — a second owner is created deliberately (site admin / migration),
# never by a member-management call. This matches the console, which has always
# omitted ``owner`` from its role picker.
MAX_ASSIGNABLE_RANK = ROLE_RANK[UserRole.administrator]

# Human-readable role labels, also used by the access matrix.
ROLE_LABELS: dict[UserRole, str] = {
    UserRole.viewer: "Viewer",
    UserRole.operator: "Data Clerk",
    UserRole.analyst: "Analyst",
    UserRole.administrator: "Administrator",
    UserRole.owner: "Owner",
}


def rank_of(user: User) -> int:
    """Hierarchy rank of a user's role (unknown roles rank as a viewer)."""
    return ROLE_RANK.get(user.role, ROLE_RANK[UserRole.viewer])


def can_assign_role(actor: User, role: UserRole) -> bool:
    """True when ``actor`` may grant ``role`` to somebody else.

    Two rules, both deliberate:

    * the role must rank **strictly below** the actor, so no one can hand out a
      bundle at or above their own — an administrator cannot create another
      administrator, and cannot make anyone (least of all themselves) an owner;
    * the role must be at or below ``MAX_ASSIGNABLE_RANK``, so ownership is
      never granted through member management.
    """
    return ROLE_RANK.get(role, 999) <= MAX_ASSIGNABLE_RANK and ROLE_RANK.get(role, 999) < rank_of(
        actor
    )


def can_manage_member(actor: User, member: User) -> bool:
    """True when ``actor`` may change or revoke ``member``'s access.

    Only strictly below the actor: peers cannot edit each other, and the owner
    — being the top rank — can be changed by no one through the API. That is
    also what makes the last owner safe: demotion and revocation are both
    unreachable for a rank nothing outranks.
    """
    return rank_of(member) < rank_of(actor)


def capabilities_for(user: User) -> frozenset[str]:
    """Capabilities granted to ``user`` by their role.

    Unknown roles (e.g. a value added to the DB enum ahead of this module) get
    the read-only bundle rather than an error, so a misconfigured role can never
    be more powerful than a viewer.
    """
    role = user.role
    return ROLE_CAPABILITIES.get(role, _VIEWER)


def has_capability(user: User, *required: str) -> bool:
    """True when the user holds every capability in ``required``."""
    held = capabilities_for(user)
    return all(cap in held for cap in required)


def has_any_capability(user: User, *required: str) -> bool:
    """True when the user holds at least one of ``required``."""
    held = capabilities_for(user)
    return any(cap in held for cap in required)


def role_label(user: User) -> str:
    return ROLE_LABELS.get(user.role, "Viewer")


def access_matrix() -> list[dict[str, object]]:
    """The role → permissions table served by ``GET /api/v1/roles``."""
    return [
        {
            "role": role.value,
            "label": ROLE_LABELS[role],
            "rank": ROLE_RANK[role],
            "assignable": ROLE_RANK[role] <= MAX_ASSIGNABLE_RANK,
            "permissions": sorted(ROLE_CAPABILITIES[role]),
        }
        for role in UserRole
    ]


def capability_groups() -> list[dict[str, object]]:
    """Capabilities grouped for display, in the order the console shows them."""
    return [
        {
            "group": "View",
            "capabilities": sorted(
                _READS
                | {
                    REPORTS_READ,
                    QUALITY_READ,
                    ALERTS_READ,
                    POLICIES_READ,
                    MODELS_READ,
                    PIPELINE_READ,
                }
            ),
        },
        {
            "group": "Data",
            "capabilities": sorted(
                {
                    INGEST_WRITE,
                    SOURCES_READ,
                    SOURCES_WRITE,
                    QUALITY_FIX,
                    ALERTS_WRITE,
                    LOCATIONS_WRITE,
                }
            ),
        },
        {
            "group": "Decide",
            "capabilities": sorted(
                {POLICIES_SIMULATE, POLICIES_WRITE, REPORTS_GENERATE, ALERTS_CONFIGURE}
            ),
        },
        {
            "group": "Platform",
            "capabilities": sorted(
                MODELLING_CAPABILITIES
                | {PIPELINE_RUN, CONNECTORS_MANAGE, WEBHOOKS_MANAGE, API_KEYS_MANAGE}
            ),
        },
        {
            "group": "Devices",
            "capabilities": sorted(
                {
                    DEVICE_SYNC,
                    DEVICES_READ,
                    DEVICES_MANAGE,
                    DEVICE_GRANTS_MANAGE,
                    DEVICE_MIRROR_REQUEST,
                    DEVICE_MIRROR_APPROVE,
                }
            ),
        },
        {
            "group": "Administer",
            "capabilities": sorted(
                {
                    WORKSPACE_MANAGE,
                    AUDIT_READ,
                    MEMBERS_MANAGE,
                    ACCESS_READ,
                    BILLING_READ,
                    BILLING_MANAGE,
                }
            ),
        },
    ]


def capabilities_of_roles(roles: Iterable[UserRole]) -> frozenset[str]:
    """Union of the bundles for several roles (used by tests and docs)."""
    out: set[str] = set()
    for role in roles:
        out |= ROLE_CAPABILITIES.get(role, frozenset())
    return frozenset(out)
