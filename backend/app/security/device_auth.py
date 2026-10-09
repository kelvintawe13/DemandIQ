"""Device credential authentication — the way an enrolled device calls the API.

Parallel to :mod:`app.security.api_key_auth`, and deliberately *not* folded into
it. A device is not a user: it has no seat, no role bundle, and no business
decision behind it. Folding device credentials into ``get_principal`` would mean
a device token could satisfy any ``current_user`` dependency in the codebase,
which is the one property this subsystem must never acquire. The two mechanisms
stay separate and every route says which one it accepts.

Credential format
-----------------
``did_<12 hex chars>.<43-char secret>`` — the same two-part idea as an API key
(``prefix`` + secret), split on a ``.`` so an operator reading a support ticket
can see at a glance which handle to look up:

* the **prefix** is a non-secret lookup handle. It selects candidate rows before
  any hash work, exactly like ``ApiKey.prefix``, and it is the only half the
  fleet console ever displays.
* the **secret** is the only half that is hashed, and the only half handed to
  :func:`app.security.hashing.verify_password`. Hashing the concatenation would
  spend part of bcrypt's 72-byte input budget on the prefix, silently weakening
  the secret as the format grows.

The presented value travels in the ``X-Device-Key`` header. It is not accepted as
a Bearer token: a device's key and a person's JWT live in different header slots
on purpose, so neither can be substituted for the other by accident.

Failure is deliberately undifferentiated
----------------------------------------
"Unknown prefix" and "wrong secret" produce the same status, the same error code
and the same message, and both paths run exactly one bcrypt verification (see
:func:`authenticate`). A response that answers a prefix probe with "no such
device" is a free enumeration oracle for a fleet, and the fleet is the one part
of this system where an attacker would like a list.

Status is checked only *after* the hash matches. A caller who already proved it
holds the credential is entitled to know the device is revoked, and that
distinction is what makes revocation diagnosable; it cannot be used to find a
device, because the hash is checked first.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.device import (
    DEVICE_STATUS_ACTIVE,
    DEVICE_STATUS_QUARANTINED,
    DEVICE_STATUS_REVOKED,
    Device,
)
from app.security.hashing import verify_password

logger = logging.getLogger(__name__)

__all__ = [
    "CREDENTIAL_HEADER",
    "PREFIX_LENGTH",
    "DevicePrincipal",
    "authenticate",
    "generate_credential",
    "get_device_principal",
    "require_device_scope",
    "split_credential",
]

#: Header the device credential is presented in. Distinct from ``X-API-Key`` on
#: purpose — see the module docstring.
CREDENTIAL_HEADER = "X-Device-Key"

_PREFIX_SCHEME = "did_"
_PREFIX_ENTROPY_BYTES = 6
#: ``did_`` plus twelve hex characters. Checked on parse so a truncated or
#: hand-typed handle is rejected before it reaches the database.
PREFIX_LENGTH = len(_PREFIX_SCHEME) + 2 * _PREFIX_ENTROPY_BYTES

# One message for every authentication failure. The caller cannot tell a bad
# prefix from a bad secret, and neither can it tell that the value was
# syntactically wrong.
_GENERIC_FAILURE = "Invalid device credential"


@dataclass(frozen=True)
class DevicePrincipal:
    """Lightweight stand-in for ``User`` on a request authenticated by a device.

    Carries only what a device-facing route can legitimately need: which device
    it is, which tenant it belongs to (every query is filtered on it), the label
    and location the console would show, and the device's base scopes.

    ``scopes`` is the *base* list from ``devices.scopes`` — the capabilities
    seeded at enrollment, never the union with live ``device_grants`` rows. A
    grant is a decision by a human with an author and an expiry, so it is
    resolved by the layer that owns grants rather than smuggled into the
    credential's identity.
    """

    device_id: uuid.UUID
    workspace_id: uuid.UUID
    label: str
    location_id: uuid.UUID | None
    scopes: list[str] = field(default_factory=list)
    status: str = DEVICE_STATUS_ACTIVE

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def generate_credential() -> tuple[str, str, str]:
    """Mint a device credential.

    Returns ``(prefix, secret, presented)``. The caller stores ``prefix`` and
    ``hash_password(secret)`` and hands ``presented`` to the device exactly once
    — only the secret half is persisted anywhere.

    32 random bytes of secret (43 urlsafe characters) keeps the presented token
    comfortably inside bcrypt's 72-byte limit, and 48 bits of prefix keeps
    collisions out of reach for any realistic fleet.
    """
    prefix = _PREFIX_SCHEME + secrets.token_hex(_PREFIX_ENTROPY_BYTES)
    secret = secrets.token_urlsafe(32)
    return prefix, secret, f"{prefix}.{secret}"


def split_credential(presented: str) -> tuple[str, str] | None:
    """Split ``prefix.secret`` into its halves, or ``None`` if it is malformed."""
    prefix, separator, secret = presented.partition(".")
    if (
        not separator
        or len(prefix) != PREFIX_LENGTH
        or not prefix.startswith(_PREFIX_SCHEME)
        or not secret
    ):
        return None
    return prefix, secret


@lru_cache(maxsize=1)
def _decoy_credential_hash() -> str:
    """A throwaway bcrypt hash used to price the unknown-prefix path.

    Computed on the first miss rather than embedded as a literal: a constant
    string in source that looks like a credential is both a rotation hazard and
    a lint problem, and the one-time hashing cost is paid only when a caller has
    already failed.
    """
    from app.security.hashing import hash_password

    return hash_password(secrets.token_urlsafe(32))


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "field": None, "reference": None}},
    )


def authenticate(db: Session, presented: str) -> Device:
    """Resolve a presented credential to its device row, or raise.

    Every path that reaches the database runs exactly one bcrypt verification, so
    the two ways of being refused cost the same:

    * a wrong secret verifies against the candidate row and fails;
    * an unknown prefix would otherwise answer without hashing anything — a
      timing oracle — so it verifies against a decoy and fails.

    A syntactically invalid value is rejected before any lookup, which reveals
    nothing: the caller already knows it is not a well-formed credential.

    Raises:
        HTTPException: 401 with :data:`_GENERIC_FAILURE` for a malformed,
            unknown or badly-secreted credential; 403 with a specific code once
            the credential is known to be good and the device is not active.
    """
    parts = split_credential(presented)
    if parts is None:
        raise _error(status.HTTP_401_UNAUTHORIZED, "unauthorized", _GENERIC_FAILURE)

    prefix, secret = parts
    # The prefix is the lookup handle, so a revoked row is still a candidate:
    # deciding its fate from the query would make "revoked" indistinguishable
    # from "never existed" and leave a caller with no way to tell a fleet
    # problem from a credential problem.
    candidates = list(db.scalars(select(Device).where(Device.credential_prefix == prefix)).all())

    matched: Device | None = None
    for row in candidates:
        if verify_password(secret, row.credential_hash):
            matched = row
            break
    if not candidates:
        verify_password(secret, _decoy_credential_hash())
    if matched is None:
        # The prefix is a non-secret handle — the console displays it — so it is
        # the one thing worth having in the log to make a failed call traceable.
        logger.info("device credential rejected prefix=%s", prefix)
        raise _error(status.HTTP_401_UNAUTHORIZED, "unauthorized", _GENERIC_FAILURE)

    if matched.status != DEVICE_STATUS_ACTIVE:
        # Reachable only with a valid credential, so naming the state leaks
        # nothing an operator could not already see, and it is the difference
        # between a device that retries forever and one that is taken out of
        # service.
        code = {
            DEVICE_STATUS_REVOKED: "device_revoked",
            DEVICE_STATUS_QUARANTINED: "device_quarantined",
        }.get(matched.status, "device_inactive")
        raise _error(
            status.HTTP_403_FORBIDDEN,
            code,
            f"This device is {matched.status}",
        )

    # Fleet health is the reason a device calls at all; a credential that works
    # but never records a last-seen makes the console's staleness display a lie.
    matched.last_seen_at = datetime.now(UTC)
    db.flush()
    return matched


def get_device_principal(
    db: Annotated[Session, Depends(get_db)],
    x_device_key: Annotated[str | None, Header(alias=CREDENTIAL_HEADER)] = None,
) -> DevicePrincipal:
    """FastAPI dependency: authenticate a request as the device it claims to be.

    Deliberately *not* a fallback from another mechanism — a route that accepts
    a device either accepts a device or accepts a human, and a missing
    ``X-Device-Key`` is simply an unauthenticated request.
    """
    if not x_device_key:
        raise _error(status.HTTP_401_UNAUTHORIZED, "unauthorized", "Device credential required")

    row = authenticate(db, x_device_key)
    return DevicePrincipal(
        device_id=row.id,
        workspace_id=row.workspace_id,
        label=row.label,
        location_id=row.location_id,
        scopes=list(row.scopes or []),
        status=row.status,
    )


def require_device_scope(principal: DevicePrincipal, scope: str) -> DevicePrincipal:
    """Guard for a device route that needs one of the device's base scopes.

    Mirrors ``api_key_auth.require_write_scope``. A device with no base scopes
    at all is refused rather than waved through: an enrollment that seeded the
    wrong list is a configuration fault, and the route that depends on it should
    fail loudly instead of running unscoped.
    """
    if not principal.has_scope(scope):
        raise _error(
            status.HTTP_403_FORBIDDEN,
            "forbidden",
            f"Device lacks the '{scope}' scope",
        )
    return principal
