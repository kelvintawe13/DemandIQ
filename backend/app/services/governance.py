"""Governance service — change request lifecycle."""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.governance import ChangeRequest
from app.models.user import User


class GovernanceError(ValueError):
    """Domain error for governance operations."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def create_change_request(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    kind: str,
    title: str,
    before_value: str | None,
    after_value: str | None,
    impact: str | None,
    requested_by: uuid.UUID,
) -> ChangeRequest:
    """Create a pending change request."""
    cr = ChangeRequest(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        kind=kind,
        title=title,
        before_value=before_value,
        after_value=after_value,
        impact=impact,
        status="pending",
        requested_by=requested_by,
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db.add(cr)
    db.flush()
    return cr


def list_change_requests(
    db: Session,
    workspace_id: uuid.UUID,
    *,
    status: str | None = None,
    kind: str | None = None,
) -> list[ChangeRequest]:
    """List change requests for a workspace, optionally filtered."""
    q = select(ChangeRequest).where(ChangeRequest.workspace_id == workspace_id)
    if status is not None:
        q = q.where(ChangeRequest.status == status)
    if kind is not None:
        q = q.where(ChangeRequest.kind == kind)
    q = q.order_by(ChangeRequest.created_at.desc())
    return list(db.scalars(q).all())


def decide_change_request(
    db: Session,
    cr: ChangeRequest,
    *,
    decided_by: User,
    decision: str,
    client: str = "web",
) -> ChangeRequest:
    """Approve or reject a change request, enforcing two-person rule.

    Args:
        db: Database session.
        cr: Change request to decide.
        decided_by: Actor deciding.
        decision: ``"approved"`` or ``"rejected"``.
        client: Client identifier for audit.

    Returns:
        Updated ``ChangeRequest``.

    Raises:
        GovernanceError: On invalid state, two-person violation, or bad decision.

    Note:
        The ``two_person_rule`` CHECK ``decided_by <> requested_by`` is
        enforced at the DB level; this function also checks it in Python
        so that callers get a 422 instead of a raw DB integrity error (which
        would surface as 500). The DB constraint remains as defence-in-depth.
    """
    if decision not in ("approved", "rejected"):
        raise GovernanceError("invalid_decision", "decision must be approved or rejected")
    if cr.status != "pending":
        raise GovernanceError("already_decided", f"Change request already {cr.status}")
    if cr.requested_by == decided_by.id:
        raise GovernanceError("two_person_rule", "Requester may not approve their own request")

    cr.status = decision
    cr.decided_by = decided_by.id
    cr.decided_at = datetime.datetime.now(datetime.UTC)
    db.flush()

    # Audit log entry for the decision
    audit = AuditLog(
        workspace_id=cr.workspace_id,
        actor_id=decided_by.id,
        action=f"change_request.{decision}",
        target=str(cr.id),
        client=client,
        meta_data={"kind": cr.kind, "title": cr.title, "decided_by": str(decided_by.id)},
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db.add(audit)
    db.flush()
    return cr
