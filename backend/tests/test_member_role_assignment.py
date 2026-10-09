"""Member management respects the role hierarchy.

Capabilities answer "may this actor do X?". The hierarchy answers a different
question — "may this actor act *on* that account?" — and the two are not the
same: an administrator holds more capabilities than an owner (the modelling
console) yet governs fewer people.

The rules asserted here:

* nobody may grant a role **at or above** their own, so an administrator cannot
  create another administrator and cannot touch an owner;
* ownership is never granted through member management at all;
* you cannot change your **own** role (that is how an administrator would
  promote themselves);
* the last owner is therefore unreachable — demotion and revocation both
  require outranking a rank that nothing outranks.

Same DB-optional style as ``test_role_capabilities.py``: a mocked session, a
dependency override for the caller, and a status-code assertion.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.user import User, UserRole
from app.security.api_key_auth import get_principal
from app.security.permissions import (
    MAX_ASSIGNABLE_RANK,
    ROLE_RANK,
    can_assign_role,
    can_manage_member,
)


def _user(ws_id: uuid.UUID, role: UserRole, email: str | None = None) -> User:
    return User(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        name=f"{role.value.title()} User",
        email=email or f"{role.value}@example.com",
        password_hash="$2b$12$dummy",
        role=role,
        email_verified=True,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _client_for(actor: User, members: list[User]) -> TestClient:
    """Client where ``db.get(User, id)`` resolves to a known roster."""

    roster = {m.id: m for m in members}
    db = MagicMock()
    db.get.side_effect = lambda model, pk, *a, **kw: roster.get(pk)
    db.add = MagicMock()
    db.flush = MagicMock()
    # No existing account matches the invited email.
    db.scalar.return_value = None
    db.scalars.return_value = MagicMock(all=MagicMock(return_value=[]))
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: actor  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: actor  # type: ignore[assignment]
    return TestClient(app)


def _invite(actor: User, role: str) -> int:
    client = _client_for(actor, [actor])
    return client.post(
        "/api/v1/members/invite",
        json={"email": "new@example.com", "name": "New Person", "role": role},
    ).status_code


def _patch(actor: User, target: User, **body: Any) -> tuple[int, str]:
    client = _client_for(actor, [actor, target])
    resp = client.patch(f"/api/v1/members/{target.id}", json=body)
    detail = ""
    if resp.status_code >= 400:
        try:
            detail = str(resp.json().get("error", {}).get("message", ""))
        except Exception:  # noqa: BLE001 - error shape is not the subject
            detail = resp.text
    return resp.status_code, detail


def _revoke(actor: User, target: User) -> int:
    client = _client_for(actor, [actor, target])
    return client.delete(f"/api/v1/members/{target.id}").status_code


# --------------------------------------------------------------------------- #
# The rules, as pure functions
# --------------------------------------------------------------------------- #


def test_only_strictly_lower_roles_are_assignable() -> None:
    ws = uuid.uuid4()
    owner = _user(ws, UserRole.owner)
    admin = _user(ws, UserRole.administrator)

    for role in (UserRole.analyst, UserRole.operator, UserRole.viewer):
        assert can_assign_role(owner, role)
        assert can_assign_role(admin, role)

    # Peers are never assignable: an administrator cannot mint administrators.
    for role in (UserRole.administrator, UserRole.owner):
        assert not can_assign_role(admin, role)

    # Ownership is not invitable, by anyone.
    for actor in (owner, admin, _user(ws, UserRole.analyst)):
        assert not can_assign_role(actor, UserRole.owner)


def test_ownership_is_above_the_assignable_ceiling() -> None:
    assert MAX_ASSIGNABLE_RANK == ROLE_RANK[UserRole.administrator]
    assert ROLE_RANK[UserRole.owner] > MAX_ASSIGNABLE_RANK
    assert ROLE_RANK[UserRole.owner] > ROLE_RANK[UserRole.administrator]
    assert ROLE_RANK[UserRole.operator] == ROLE_RANK[UserRole.analyst] - 1


def test_members_are_only_manageable_from_above() -> None:
    ws = uuid.uuid4()
    owner = _user(ws, UserRole.owner)
    admin = _user(ws, UserRole.administrator)
    analyst = _user(ws, UserRole.analyst)
    clerk = _user(ws, UserRole.operator)

    assert can_manage_member(owner, admin)
    assert can_manage_member(admin, clerk)
    assert can_manage_member(admin, analyst)

    # No one outranks an owner, so an owner can be changed by no one.
    assert not can_manage_member(admin, owner)
    # Peers cannot edit each other.
    assert not can_manage_member(admin, _user(ws, UserRole.administrator))
    assert not can_manage_member(analyst, _user(ws, UserRole.analyst))
    # And nobody manages themselves through this path.
    assert not can_manage_member(admin, admin)
    assert all(not can_manage_member(actor, actor) for actor in (owner, admin, analyst, clerk))


def test_the_last_owner_cannot_be_demoted_or_revoked() -> None:
    """The guarantee that keeps a workspace billable, whatever the roster."""

    ws = uuid.uuid4()
    owner = _user(ws, UserRole.owner)
    admin = _user(ws, UserRole.administrator)

    for actor in (owner, admin):
        assert not can_manage_member(
            actor, owner
        ), f"{actor.role.value} must never be able to change an owner"


# --------------------------------------------------------------------------- #
# The rules, through the API
# --------------------------------------------------------------------------- #


def test_administrator_cannot_invite_an_owner() -> None:
    ws = uuid.uuid4()
    assert _invite(_user(ws, UserRole.administrator), "owner") == 403


def test_administrator_cannot_invite_a_peer_administrator() -> None:
    ws = uuid.uuid4()
    assert _invite(_user(ws, UserRole.administrator), "administrator") == 403


def test_owner_can_invite_an_administrator() -> None:
    ws = uuid.uuid4()
    assert _invite(_user(ws, UserRole.owner), "administrator") == 201


def test_administrator_can_invite_the_roles_below_it() -> None:
    ws = uuid.uuid4()
    admin = _user(ws, UserRole.administrator)
    for role in ("analyst", "operator", "viewer"):
        assert _invite(admin, role) == 201, role


def test_analyst_and_clerk_cannot_reach_member_management_at_all() -> None:
    ws = uuid.uuid4()
    assert _invite(_user(ws, UserRole.analyst), "viewer") == 403
    assert _invite(_user(ws, UserRole.operator), "viewer") == 403


def test_administrator_cannot_promote_themselves() -> None:
    ws = uuid.uuid4()
    admin = _user(ws, UserRole.administrator)
    status, message = _patch(admin, admin, role="owner")
    assert status == 400
    assert "own role" in message


def test_administrator_cannot_promote_a_clerk_to_owner() -> None:
    ws = uuid.uuid4()
    admin = _user(ws, UserRole.administrator)
    clerk = _user(ws, UserRole.operator)
    status, message = _patch(admin, clerk, role="owner")
    assert status == 403
    assert "owner" in message.lower()


def test_administrator_cannot_touch_an_owner() -> None:
    ws = uuid.uuid4()
    admin = _user(ws, UserRole.administrator)
    owner = _user(ws, UserRole.owner)

    status, _ = _patch(admin, owner, role="viewer")
    assert status == 403
    assert _revoke(admin, owner) == 403
    # Even a rename is refused: you may not edit accounts above your own.
    assert _patch(admin, owner, name="Renamed")[0] == 403


def test_owner_moves_a_clerk_up_to_analyst() -> None:
    ws = uuid.uuid4()
    owner = _user(ws, UserRole.owner)
    clerk = _user(ws, UserRole.operator)
    status, _ = _patch(owner, clerk, role="analyst")
    assert status == 200
    assert clerk.role is UserRole.analyst


def test_owner_cannot_make_a_second_owner_through_member_management() -> None:
    ws = uuid.uuid4()
    owner = _user(ws, UserRole.owner)
    admin = _user(ws, UserRole.administrator)
    status, _ = _patch(owner, admin, role="owner")
    assert status == 403


def test_owner_cannot_demote_another_owner() -> None:
    """Two owners cannot unseat each other — that is a support action."""

    ws = uuid.uuid4()
    owner = _user(ws, UserRole.owner)
    peer = _user(ws, UserRole.owner, email="co-owner@example.com")
    assert _patch(owner, peer, role="viewer")[0] == 403
    assert _revoke(owner, peer) == 403


def test_administrator_can_revoke_below_its_rank() -> None:
    ws = uuid.uuid4()
    admin = _user(ws, UserRole.administrator)
    clerk = _user(ws, UserRole.operator)
    assert _revoke(admin, clerk) == 204
    assert clerk.deactivated_at is not None


def test_administrator_cannot_revoke_itself() -> None:
    ws = uuid.uuid4()
    admin = _user(ws, UserRole.administrator)
    assert _revoke(admin, admin) == 400


def test_renaming_yourself_is_still_allowed() -> None:
    ws = uuid.uuid4()
    admin = _user(ws, UserRole.administrator)
    status, _ = _patch(admin, admin, name="New Name")
    assert status == 200
    assert admin.name == "New Name"


def test_unknown_roles_are_rejected_before_the_hierarchy() -> None:
    ws = uuid.uuid4()
    owner = _user(ws, UserRole.owner)
    clerk = _user(ws, UserRole.operator)
    assert _patch(owner, clerk, role="superuser")[0] == 400
    assert _invite(owner, "superuser") == 400


def test_the_roles_endpoint_publishes_the_hierarchy() -> None:
    """/roles drives the console's picker, so it must carry rank + assignable."""

    ws = uuid.uuid4()
    owner = _user(ws, UserRole.owner)
    client = _client_for(owner, [owner])
    rows = {row["role"]: row for row in client.get("/api/v1/roles").json()["data"]}

    assert rows["owner"]["rank"] > rows["administrator"]["rank"] > rows["analyst"]["rank"]
    assert rows["analyst"]["rank"] > rows["operator"]["rank"] > rows["viewer"]["rank"]
    assert rows["owner"]["assignable"] is False
    assert rows["administrator"]["assignable"] is True
    assert rows["analyst"]["assignable"] is True
    assert rows["viewer"]["rank"] == 0
