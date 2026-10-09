"""Governance endpoints — change requests, members, roles, audit, pipeline.

* GET  /api/v1/change-requests
* POST /api/v1/change-requests
* POST /api/v1/change-requests/{id}/approve
* POST /api/v1/change-requests/{id}/reject
* GET  /api/v1/members
* POST /api/v1/members/invite
* PATCH /api/v1/members/{id}
* DELETE /api/v1/members/{id}
* GET  /api/v1/roles
* GET  /api/v1/audit-log          (also in access router, kept for contract parity)
* GET  /api/v1/pipeline/runs
* POST /api/v1/pipeline/runs/{id}/rerun
* GET  /api/v1/pipeline/drift
* GET  /api/v1/pipeline/schedule        (cadence + last/next run per job)
* POST /api/v1/pipeline/jobs/{job_type}/run
* POST /api/v1/pipeline/queue/drain      (run queued jobs now)
"""

from __future__ import annotations

import datetime
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.jobs.runner import JOB_SCHEDULE, JOBS, run_job
from app.models.audit import AuditLog
from app.models.governance import ChangeRequest
from app.models.pipeline import PipelineRun
from app.models.user import User, UserRole
from app.security.permissions import (
    ACCESS_READ,
    APPROVALS_MANAGE,
    MEMBERS_MANAGE,
    PIPELINE_READ,
    PIPELINE_RUN,
    ROLE_LABELS,
    access_matrix,
    can_assign_role,
    can_manage_member,
    capability_groups,
    has_capability,
)
from app.services.drift import compute_drift
from app.services.governance import (
    GovernanceError,
    create_change_request,
    decide_change_request,
    list_change_requests,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["governance"])


def _error(code: int, err: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err, "message": msg, "field": field, "reference": None}},
    )


def _roles_reader(user: Annotated[User, Depends(get_current_user)]) -> None:
    """The role matrix is governance information, not public reference data."""
    _require(user, ACCESS_READ)


def _require(user: User, *required: str) -> None:
    """403 unless the caller holds every listed capability.

    Governance routes check the capability they actually need — approving a
    change request is not the same power as managing members, even though the
    owner and administrator hold both today.
    """
    if not has_capability(user, *required):
        raise _error(403, "forbidden", "Insufficient permissions for this action")


def _client_from_request(request: Request) -> str:
    header = request.headers.get("x-client")
    if header in ("web", "mobile", "api", "system"):
        return header
    return "web"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ChangeRequestCreate(BaseModel):
    """Body for POST /change-requests."""

    kind: str = Field(description="model_promotion|policy_change|access_grant")
    title: str = Field(description="Human-readable title")
    before_value: str | None = Field(default=None)
    after_value: str | None = Field(default=None)
    impact: str | None = Field(default=None)


class MemberInviteRequest(BaseModel):
    """Body for POST /members/invite."""

    email: str
    role: str = Field(description="owner|administrator|analyst|operator|viewer")
    name: str | None = Field(default=None)


class MemberPatchRequest(BaseModel):
    """Body for PATCH /members/{id}."""

    role: str | None = Field(default=None)
    name: str | None = Field(default=None)


# ---------------------------------------------------------------------------
# Change requests
# ---------------------------------------------------------------------------


@router.get("/api/v1/change-requests", response_model=dict)
def list_crs(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
) -> dict[str, Any]:
    """List change requests (admin)."""
    _require(current_user, APPROVALS_MANAGE)
    rows = list_change_requests(db, current_user.workspace_id, status=status, kind=kind)

    # `requested_by`/`decided_by` are user ids. The approvals screen used to print
    # the first eight characters of one as "Decided by c6f6e213" — an opaque
    # reference where a person's name belongs — so the names are resolved here,
    # in one query, rather than leaving every client to look them up. The ids stay
    # in the payload: they are the stable key, the names are the display.
    actor_ids = {r.requested_by for r in rows}
    actor_ids |= {r.decided_by for r in rows if r.decided_by is not None}
    names: dict[uuid.UUID, str | None] = {}
    if actor_ids:
        try:
            names = {
                row[0]: row[1]
                for row in db.execute(
                    select(User.id, User.name).where(User.id.in_(actor_ids))
                ).all()
            }
        except Exception:
            # Best-effort, like the other reads on this router: a mocked session in
            # tests has no users table, and a name is never worth failing the list.
            logger.debug("could not resolve change-request actor names", exc_info=True)

    return {
        "data": [
            {
                "id": str(r.id),
                "workspace_id": str(r.workspace_id),
                "kind": r.kind,
                "title": r.title,
                "before_value": r.before_value,
                "after_value": r.after_value,
                "impact": r.impact,
                "status": r.status,
                "requested_by": str(r.requested_by),
                "requested_by_name": names.get(r.requested_by),
                "decided_by": str(r.decided_by) if r.decided_by else None,
                "decided_by_name": names.get(r.decided_by) if r.decided_by else None,
                "decided_at": r.decided_at.isoformat() if r.decided_at else None,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.post("/api/v1/change-requests", response_model=dict, status_code=201)
def create_cr(
    payload: ChangeRequestCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Create a change request (admin)."""
    _require(current_user, APPROVALS_MANAGE)
    cr = create_change_request(
        db,
        workspace_id=current_user.workspace_id,
        kind=payload.kind,
        title=payload.title,
        before_value=payload.before_value,
        after_value=payload.after_value,
        impact=payload.impact,
        requested_by=current_user.id,
    )
    return {
        "id": str(cr.id),
        "workspace_id": str(cr.workspace_id),
        "kind": cr.kind,
        "title": cr.title,
        "before_value": cr.before_value,
        "after_value": cr.after_value,
        "impact": cr.impact,
        "status": cr.status,
        "requested_by": str(cr.requested_by),
        "created_at": cr.created_at.isoformat(),
    }


def _decide(
    cr_id: uuid.UUID,
    decision: str,
    request: Request,
    db: Session,
    current_user: User,
) -> dict[str, Any]:
    cr: ChangeRequest | None = db.get(ChangeRequest, cr_id)
    if cr is None or cr.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Change request not found")
    try:
        updated = decide_change_request(
            db, cr, decided_by=current_user, decision=decision, client=_client_from_request(request)
        )
    except GovernanceError as exc:
        if exc.code == "two_person_rule":
            raise _error(422, "two_person_rule", str(exc)) from exc
        raise _error(422, exc.code, str(exc)) from exc

    return {
        "id": str(updated.id),
        "status": updated.status,
        "decided_by": str(updated.decided_by) if updated.decided_by else None,
        "decided_at": updated.decided_at.isoformat() if updated.decided_at else None,
    }


@router.post("/api/v1/change-requests/{cr_id}/approve", response_model=dict)
def approve_cr(
    cr_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Approve a change request — requester may not approve their own."""
    _require(current_user, APPROVALS_MANAGE)
    return _decide(cr_id, "approved", request, db, current_user)


@router.post("/api/v1/change-requests/{cr_id}/reject", response_model=dict)
def reject_cr(
    cr_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Reject a change request."""
    _require(current_user, APPROVALS_MANAGE)
    return _decide(cr_id, "rejected", request, db, current_user)


# ---------------------------------------------------------------------------
# Members & roles
# ---------------------------------------------------------------------------


@router.get("/api/v1/members", response_model=dict)
def list_members(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Roster for the workspace (admin)."""
    _require(current_user, MEMBERS_MANAGE)
    rows = list(
        db.scalars(select(User).where(User.workspace_id == current_user.workspace_id)).all()
    )
    return {
        "data": [
            {
                "id": str(u.id),
                "name": u.name,
                "email": u.email,
                "role": u.role.value if hasattr(u.role, "value") else str(u.role),
                "email_verified": u.email_verified,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for r in rows
            for u in [r]
        ]
    }


@router.post("/api/v1/members/invite", response_model=dict, status_code=201)
def invite_member(
    payload: MemberInviteRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Invite a member (stub — creates a deactivated placeholder user)."""
    _require(current_user, MEMBERS_MANAGE)
    # Validate role
    try:
        role = UserRole(payload.role)
    except ValueError:
        raise _error(400, "invalid_role", f"Unknown role '{payload.role}'", field="role") from None

    # You can only grant a role below your own, and never ownership itself.
    if not can_assign_role(current_user, role):
        raise _error(
            403,
            "forbidden",
            f"You cannot grant the '{ROLE_LABELS.get(role, role.value)}' role",
            field="role",
        )

    # Check duplicate email
    existing = db.scalar(select(User).where(User.email == payload.email.lower()))
    if existing is not None and existing.workspace_id == current_user.workspace_id:
        raise _error(409, "conflict", "Email already in workspace", field="email")

    # Create placeholder invitee (in real impl send email)
    invitee = User(
        id=uuid.uuid4(),
        workspace_id=current_user.workspace_id,
        name=payload.name or payload.email.split("@")[0],
        email=payload.email.lower(),
        password_hash=None,
        role=role,
        email_verified=False,
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db.add(invitee)
    db.flush()

    # Audit
    audit = AuditLog(
        workspace_id=current_user.workspace_id,
        actor_id=current_user.id,
        action="member.invite",
        target=str(invitee.id),
        client=_client_from_request(request),
        meta_data={"email": invitee.email, "role": role.value},
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db.add(audit)
    db.flush()

    return {
        "id": str(invitee.id),
        "email": invitee.email,
        "role": role.value,
        "status": "invited",
    }


@router.patch("/api/v1/members/{member_id}", response_model=dict)
def patch_member(
    member_id: uuid.UUID,
    payload: MemberPatchRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Change role or name (admin)."""
    _require(current_user, MEMBERS_MANAGE)
    member: User | None = db.get(User, member_id)
    if member is None or member.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Member not found")
    # You may only edit accounts below your own rank, so peers cannot demote or
    # rename each other and no one can reach an owner (see permissions.py).
    is_self = member.id == current_user.id
    if not is_self and not can_manage_member(current_user, member):
        raise _error(
            403,
            "forbidden",
            f"You cannot change the access of a {ROLE_LABELS.get(member.role, 'peer')}",
        )
    before_role = member.role.value if hasattr(member.role, "value") else str(member.role)
    if payload.role is not None:
        # Changing your *own* role is how an administrator would escalate
        # themselves, so it is refused outright rather than ranked.
        if is_self:
            raise _error(400, "invalid_request", "You cannot change your own role")
        try:
            new_role = UserRole(payload.role)
        except ValueError:
            raise _error(
                400, "invalid_role", f"Unknown role '{payload.role}'", field="role"
            ) from None
        if not can_assign_role(current_user, new_role):
            raise _error(
                403,
                "forbidden",
                f"You cannot grant the '{ROLE_LABELS.get(new_role, new_role.value)}' role",
                field="role",
            )
        member.role = new_role  # type: ignore[assignment]
    if payload.name is not None:
        member.name = payload.name
    db.flush()

    audit = AuditLog(
        workspace_id=current_user.workspace_id,
        actor_id=current_user.id,
        action="member.update",
        target=str(member.id),
        client=_client_from_request(request),
        meta_data={
            "before_role": before_role,
            "after_role": member.role.value if hasattr(member.role, "value") else str(member.role),
        },
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db.add(audit)
    db.flush()

    return {
        "id": str(member.id),
        "name": member.name,
        "email": member.email,
        "role": member.role.value if hasattr(member.role, "value") else str(member.role),
    }


@router.delete("/api/v1/members/{member_id}", status_code=204)
def delete_member(
    member_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Revoke access (soft-deactivate)."""
    _require(current_user, MEMBERS_MANAGE)
    if member_id == current_user.id:
        raise _error(400, "invalid_request", "Cannot revoke own access")
    member: User | None = db.get(User, member_id)
    if member is None or member.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Member not found")
    if not can_manage_member(current_user, member):
        raise _error(
            403,
            "forbidden",
            f"You cannot revoke the access of a {ROLE_LABELS.get(member.role, 'peer')}",
        )
    member.deactivated_at = datetime.datetime.now(datetime.UTC)
    db.flush()

    audit = AuditLog(
        workspace_id=current_user.workspace_id,
        actor_id=current_user.id,
        action="member.revoke",
        target=str(member.id),
        client=_client_from_request(request),
        meta_data={"email": member.email},
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db.add(audit)
    db.flush()


@router.get("/api/v1/roles", response_model=dict, dependencies=[Depends(_roles_reader)])
def list_roles(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Role definitions and permission matrix."""
    _ = db
    _ = current_user
    # Generated from the capability model, so this table cannot drift from what
    # the API actually enforces (it used to be a hand-written second copy).
    return {"data": access_matrix(), "groups": capability_groups()}


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _pipeline_run_dict(r: PipelineRun) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "job_type": r.job_type,
        "status": r.status,
        "rows_processed": r.rows_processed,
        "duration_ms": r.duration_ms,
        "detail": r.detail,
        "started_at": r.started_at.isoformat() if r.started_at else None,
    }


@router.get("/api/v1/pipeline/runs", response_model=dict)
def list_pipeline_runs(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Recent pipeline job runs for the workspace (admin).

    Reads the real ``pipeline_runs`` table written by the job runner. An empty
    list is the honest answer when nothing has run yet — the console renders
    its empty state rather than invented rows.
    """
    _require(current_user, PIPELINE_READ)
    try:
        rows = [
            r
            for r in db.scalars(
                select(PipelineRun)
                .where(PipelineRun.workspace_id == current_user.workspace_id)
                .order_by(PipelineRun.started_at.desc())
                .limit(limit)
            ).all()
            if isinstance(r, PipelineRun)
        ]
    except Exception:
        rows = []
    return {"data": [_pipeline_run_dict(r) for r in rows]}


@router.post("/api/v1/pipeline/runs/{run_id}/rerun", response_model=dict)
def rerun_pipeline(
    run_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Re-run a job, recording a fresh ``pipeline_runs`` row.

    Triggering work is the administrator's job; the owner can watch pipeline
    health but not spend compute on it.
    """
    _require(current_user, PIPELINE_RUN)
    run: PipelineRun | None = db.get(PipelineRun, run_id)
    if run is None or run.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Pipeline run not found")
    if run.job_type not in JOBS:
        raise _error(400, "unsupported_job_type", f"No runner for '{run.job_type}'")
    new_run = run_job(
        db,
        workspace_id=current_user.workspace_id,
        job_type=run.job_type,
        detail=f"rerun of {run_id}",
    )
    # The run's own `detail` already says "rerun of <id>", so the structured
    # duplicate is dropped rather than returned unread.
    return {
        "id": str(new_run.id),
        "status": new_run.status,
        "job_type": new_run.job_type,
    }


@router.get("/api/v1/pipeline/drift", response_model=dict)
def get_drift(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Live vs training distribution signals (PSI against the model's window)."""
    _require(current_user, PIPELINE_READ)
    return compute_drift(db, current_user.workspace_id)


@router.get("/api/v1/pipeline/schedule", response_model=dict)
def get_schedule(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Cron schedule for each job type, with its last and next run.

    The cadence comes from :data:`JOB_SCHEDULE` (one source of truth shared with
    the scheduler), ``next_run`` is computed from the cron field, and
    ``last_run`` is the newest ``pipeline_runs`` row for that job type — so the
    console can show "last ran 03:20 · next in 6h" without a second endpoint.
    """
    _require(current_user, PIPELINE_READ)
    return {"data": _schedule_rows(db, current_user.workspace_id)}


def _schedule_rows(db: Session, workspace_id: uuid.UUID) -> list[dict[str, Any]]:
    """Per-job cadence plus its most recent run and its next tick."""
    from app.jobs.scheduler import next_run_after

    now = datetime.datetime.now(datetime.UTC)
    latest: dict[str, PipelineRun] = {}
    try:
        rows = [
            r
            for r in db.scalars(
                select(PipelineRun)
                .where(PipelineRun.workspace_id == workspace_id)
                .order_by(PipelineRun.started_at.desc())
                .limit(200)
            ).all()
            if isinstance(r, PipelineRun)
        ]
        # Rows arrive newest-first, so the first hit wins for each job type.
        for row in rows:
            latest.setdefault(row.job_type, row)
    except Exception:
        latest = {}

    data: list[dict[str, Any]] = []
    for job_type, sched in JOB_SCHEDULE.items():
        last = latest.get(job_type)
        nxt = next_run_after(sched["cron"], now)
        data.append(
            {
                "job_type": job_type,
                "cadence": sched["cadence"],
                "cron": sched["cron"],
                "last_run": _pipeline_run_dict(last) if last is not None else None,
                "next_run": nxt.isoformat() if nxt is not None else None,
            }
        )
    return data


@router.post("/api/v1/pipeline/queue/drain", response_model=dict)
def drain_queue(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Run the workspace's queued jobs now, and reap abandoned ones (admin).

    A queued run (``POST /sources/{id}/sync``) is completed by the worker
    process or the in-process scheduler. When neither is running — a single-node
    install, a debugging session — an operator can push the queue through from
    here instead of waiting for a tick that will never come.
    """
    from app.jobs.runner import execute_queued, recover_stale_runs

    _require(current_user, PIPELINE_RUN)
    recovered = recover_stale_runs(db, workspace_id=current_user.workspace_id)
    processed = execute_queued(db, workspace_id=current_user.workspace_id)
    try:
        remaining = len(
            [
                r
                for r in db.scalars(
                    select(PipelineRun).where(
                        PipelineRun.workspace_id == current_user.workspace_id,
                        PipelineRun.status == "queued",
                    )
                ).all()
                if isinstance(r, PipelineRun)
            ]
        )
    except Exception:
        remaining = 0
    return {
        "processed": processed,
        "recovered": recovered,
        "still_queued": remaining,
    }


@router.post("/api/v1/pipeline/jobs/{job_type}/run", response_model=dict)
def run_pipeline_job(
    job_type: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Run one scheduled job immediately for this workspace (admin).

    Same functions the scheduler drives, executed on demand — the manual
    counterpart to the cron cadence for operators who do not want to wait for
    the next tick. The ``pipeline_runs`` row reports exactly what ran.
    """
    _require(current_user, PIPELINE_RUN)
    if job_type not in JOBS:
        raise _error(404, "unknown_job", f"Unknown job '{job_type}'", field="job_type")
    run = run_job(
        db,
        workspace_id=current_user.workspace_id,
        job_type=job_type,
        detail=f"manual run of {job_type}",
    )
    return {**_pipeline_run_dict(run), "ok": run.status == "success"}
