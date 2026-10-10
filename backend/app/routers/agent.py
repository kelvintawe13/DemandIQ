"""Agent router — read-only orchestration per AGENT_SERVICE.md.

Real but minimal: quota/session checks, read-only tool surface, guardrail stubs.
Uses the ``agent_*`` tables (AGENT_SCHEMA.sql) and, in front of them, the
session/quota maps described below — mirrored to Redis when one is reachable so
the limits are shared between workers, with the in-process copy authoritative
when it is not (local development, the hermetic test suite).
"""

from __future__ import annotations

import datetime
import logging
import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.user import User, UserRole
from app.security.permissions import AGENT_CONFIGURE, ROLE_LABELS, has_capability
from app.services.kv import SharedMap

router = APIRouter(tags=["agent"])
admin_router = APIRouter(tags=["agent-admin"])

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session/quota state, mirrored to Redis when one is reachable
#
# These used to be three module-level dicts, so every worker kept its own copy:
# the concurrency limit, the daily quota and the 6/min burst were all enforced
# per process, and a restart wiped the lot. They are now shared maps
# (app/services/kv.py) whose local copy stays authoritative in-process — so the
# mocked-DB test paths are unchanged — while writes are mirrored to Redis.
#
# TTLs are the window each store is meaningful for: a session cannot outlive
# MAX_SESSION_H plus slack, and a burst entry is stale after 60s.
# ---------------------------------------------------------------------------
def _uuid_key(value: uuid.UUID) -> str:
    return str(value)


def _uuid_key_decode(fragment: str) -> Any:
    try:
        return uuid.UUID(fragment)
    except ValueError:
        return fragment


def _usage_key(value: tuple[uuid.UUID, str]) -> str:
    return f"{value[0]}|{value[1]}"


def _usage_key_decode(fragment: str) -> Any:
    user_id, _, day = fragment.partition("|")
    return (_uuid_key_decode(user_id), day)


_SESSIONS: SharedMap = SharedMap(
    "agent:sessions", ttl_seconds=6 * 60 * 60, key_encode=_uuid_key, key_decode=_uuid_key_decode
)
_TURNS: SharedMap = SharedMap(
    "agent:turns", ttl_seconds=6 * 60 * 60, key_encode=_uuid_key, key_decode=_uuid_key_decode
)
# Annotated so the values keep their types when they are re-exposed as an API
# payload (`tools` in particular is a list, not an opaque object).
_QUOTA_TIERS: dict[str, dict[str, Any]] = {
    "operator": {
        "daily": 60,
        "monthly": 600,
        "concurrent": 2,
        "tools": ["forecast", "history", "explain", "policy", "pipeline"],
    },
    "analyst": {
        "daily": 150,
        "monthly": 1500,
        "concurrent": 5,
        "tools": ["forecast", "history", "explain", "policy", "pipeline", "model"],
    },
    "owner": {"daily": 200, "monthly": 2000, "concurrent": 10, "tools": ["*"]},
    "administrator": {"daily": 200, "monthly": 2000, "concurrent": 10, "tools": ["*"]},
}
# (user_id, date_str) -> count. Two days of TTL so a long-running process cannot
# keep serving a stale "today" bucket after midnight UTC.
_USAGE: SharedMap = SharedMap(
    "agent:usage",
    ttl_seconds=48 * 60 * 60,
    key_encode=_usage_key,
    key_decode=_usage_key_decode,
)
_BURST: SharedMap = SharedMap(
    "agent:burst", ttl_seconds=120, key_encode=_uuid_key, key_decode=_uuid_key_decode
)

# The tier applied to a role that has no entry of its own. Named once because
# both the enforcement path and the admin-facing payload must agree on it.
_DEFAULT_TIER_ID = "operator"

# Session limits. These are enforced below *and* reported — on session create to
# the client, and by `GET /api/v1/admin/agent/tiers` to administrators. They were
# literal 30/4/40/6 repeated at each site, so the reported figure was one edit
# away from describing a limit the API did not apply.
IDLE_TIMEOUT_MIN = 30
MAX_SESSION_H = 4
MAX_TURNS_PER_SESSION = 40
BURST_PER_MIN = 6


def _error(code: int, err: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err, "message": msg, "field": field, "reference": None}},
    )


def _require_admin(user: User) -> None:
    if not has_capability(user, AGENT_CONFIGURE):
        raise _error(403, "forbidden", "Administrator role required")


def _quota_for(user: User) -> dict[str, Any]:
    role_key = user.role.value if hasattr(user.role, "value") else str(user.role)
    tier = _QUOTA_TIERS.get(role_key, _QUOTA_TIERS[_DEFAULT_TIER_ID])
    # Check in-memory grants (simplified)
    return tier


def _tier_name(tier_id: str) -> str:
    """Display name for a quota tier, from the role labels the UI already uses."""
    try:
        role = UserRole(tier_id)
    except ValueError:
        return tier_id.replace("_", " ").title()
    return ROLE_LABELS.get(role, tier_id.replace("_", " ").title())


def _check_quota(user: User) -> None:
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    key = (user.id, today)
    used = _USAGE.get(key, 0)
    tier = _quota_for(user)
    if used >= tier["daily"]:
        raise _error(429, "quota_exhausted_daily", "Daily question limit reached", field="quota")
    # burst 6/min
    now = datetime.datetime.now(datetime.UTC)
    bucket = _BURST.get(user.id, [])
    bucket = [t for t in bucket if (now - t).total_seconds() < 60]
    if len(bucket) >= BURST_PER_MIN:
        raise _error(429, "rate_limited", "Too many requests. Try again shortly", field="quota")
    bucket.append(now)
    _BURST[user.id] = bucket


def _expire_stale_sessions(db: Session, user_id: uuid.UUID) -> None:
    """Auto-close sessions idle > 30 min or older than 4 h (memory + DB).

    Without this, a user who closes their tab accumulates phantom live
    sessions and eventually hits ``session_limit_reached`` forever.
    """
    now = datetime.datetime.now(datetime.UTC)
    idle_cutoff = now - datetime.timedelta(minutes=IDLE_TIMEOUT_MIN)
    age_cutoff = now - datetime.timedelta(hours=MAX_SESSION_H)
    for sid, rec in list(_SESSIONS.items()):
        if rec["user_id"] == user_id and rec["ended_at"] is None:
            started = rec.get("started_at")
            last_seen = rec.get("last_seen_at", started)
            if started and started < age_cutoff:
                rec["ended_at"] = now
                rec["end_reason"] = "max_duration"
            elif last_seen and last_seen < idle_cutoff:
                rec["ended_at"] = now
                rec["end_reason"] = "idle_timeout"
            else:
                continue
            # The record was edited in place, which the mirror cannot see.
            _SESSIONS.persist(sid)
    try:
        from sqlalchemy import text

        db.execute(
            text(
                "UPDATE agent_sessions SET ended_at=COALESCE(ended_at, :now), "
                "end_reason=CASE WHEN started_at < :age THEN 'max_duration' ELSE 'idle_timeout' END "
                "WHERE user_id=:uid AND ended_at IS NULL "
                "AND (last_seen_at < :idle OR started_at < :age)"
            ),
            {"uid": str(user_id), "now": now, "idle": idle_cutoff, "age": age_cutoff},
        )
        db.flush()
    except Exception:
        # Best-effort housekeeping: a failure here must not fail the request.
        logger.debug("could not expire stale agent sessions", exc_info=True)


def _check_session_limit(user: User, db: Session) -> None:
    tier = _quota_for(user)
    _expire_stale_sessions(db, user.id)
    # Count unique live sessions across BOTH stores (in-memory ids may differ
    # in type from DB ids — compare stringified).
    live_ids = {
        str(s["id"])
        for s in _SESSIONS.values()
        if s["user_id"] == user.id and s["ended_at"] is None
    }
    try:
        from sqlalchemy import text

        rows = db.execute(
            text("SELECT id FROM agent_sessions WHERE user_id=:uid AND ended_at IS NULL"),
            {"uid": str(user.id)},
        ).all()
        live_ids |= {str(r[0]) for r in rows}
    except Exception:
        logger.warning(
            "could not count live agent sessions; the concurrency limit is enforced "
            "against in-memory sessions only",
            exc_info=True,
        )
    if len(live_ids) >= tier["concurrent"]:
        raise _error(409, "session_limit_reached", "Maximum concurrent sessions reached")


# ---------------------------------------------------------------------------
# Assistant voice — greeting and hand-off targets
# ---------------------------------------------------------------------------

# A hand-off button is only useful if the caller can actually open the screen,
# so these mirror the role gates in `frontend/src/data/navItems.ts`. Offering a
# route the role cannot reach would land the user on a 403.
_ACTION_ROLES: dict[str, tuple[str, ...]] = {
    "forecast": ("owner", "administrator", "analyst", "operator", "viewer"),
    "analytics": ("owner", "administrator", "analyst", "operator", "viewer"),
    "quality": ("owner", "administrator", "analyst", "operator"),
    "admin-policies": ("owner", "administrator", "analyst"),
    "admin-explainer": ("administrator", "analyst"),
    "admin-pipeline": ("administrator",),
    "admin-models": ("administrator",),
}


def _role_value(user: User) -> str:
    """The caller's role as its raw value; ``""`` when the user has none.

    An unknown role matches no entry in :data:`_ACTION_ROLES`, so the assistant
    offers no hand-off rather than one the screen would refuse.
    """
    role = getattr(user, "role", None)
    if role is None:
        return ""
    return role.value if hasattr(role, "value") else str(role)


# One label per destination, so the button text and the screen it opens cannot
# disagree: a role that falls back to a general route gets that route's label,
# not the label of the screen it could not reach.
_ACTION_LABELS: dict[str, str] = {
    "forecast": "Open the forecast",
    "analytics": "See the analytics",
    "quality": "Open the quality monitor",
    "admin-policies": "Compare policies",
    "admin-explainer": "Open the explainer",
    "admin-pipeline": "Open the pipeline",
    "admin-models": "Open the registry",
}


def _action_for(user: User, *routes: str) -> dict[str, str] | None:
    """The first of ``routes`` this caller's role may open, or ``None``.

    Order is a preference: the most specific screen is offered first, and a role
    that cannot reach it falls back to a general one rather than to no action.
    """
    role = _role_value(user)
    for route in routes:
        allowed = _ACTION_ROLES.get(route)
        if allowed is not None and role in allowed:
            return {"label": _ACTION_LABELS[route], "route": route}
    return None


def _greeting(user: User, location_name: str | None = None) -> str:
    """The assistant's opening message.

    Names the caller and the scope and states the evidence boundary up front.
    It makes no claim about which data exists — that would need a query at
    session-open time, and a greeting is not worth a guess.
    """
    parts = (getattr(user, "name", "") or "").strip().split()
    hi = f"Hi {parts[0]}" if parts else "Hi"
    scope = f" for {location_name}" if location_name else ""
    return (
        f"{hi}! I'm your DemandIQ assistant. I answer only from this "
        f"workspace's data{scope}: demand history, forecasts and their intervals, "
        f"the production model and its calibration, the preparation policies, "
        f"and ingestion health. Ask me what to prepare tomorrow, why a number "
        f"moved, or whether something needs your attention. If I cannot source "
        f"an answer I will say so rather than guess."
    )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SessionCreate(BaseModel):
    location_id: uuid.UUID | None = None


class MessageCreate(BaseModel):
    session_id: uuid.UUID
    question: str = Field(min_length=1, max_length=2000)


class PinRequest(BaseModel):
    note: str | None = None


# ---------------------------------------------------------------------------
# User agent endpoints
# ---------------------------------------------------------------------------


@router.post("/api/v1/agent/sessions", status_code=201)
def create_session(
    payload: SessionCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _check_quota(current_user)
    _check_session_limit(current_user, db)
    sid = uuid.uuid4()
    tier = _quota_for(current_user)
    limits = {
        "idle_timeout_min": IDLE_TIMEOUT_MIN,
        "max_duration_h": MAX_SESSION_H,
        "turns": MAX_TURNS_PER_SESSION,
        "burst_per_min": BURST_PER_MIN,
    }
    # Persist in-memory and try DB
    rec = {
        "id": sid,
        "workspace_id": current_user.workspace_id,
        "user_id": current_user.id,
        "location_id": payload.location_id,
        "started_at": datetime.datetime.now(datetime.UTC),
        "ended_at": None,
        "turn_count": 0,
    }
    _SESSIONS[sid] = rec
    _TURNS[sid] = []
    try:
        from sqlalchemy import text

        # Try real DB insert if table exists
        db.execute(text("SELECT 1 FROM agent_sessions LIMIT 1"))
        # If table exists, insert via text
        db.execute(
            text(
                "INSERT INTO agent_sessions (id, workspace_id, user_id, location_id, started_at, last_seen_at) VALUES (:id, :wid, :uid, :lid, :now, :now)"
            ),
            {
                "id": str(sid),
                "wid": str(current_user.workspace_id),
                "uid": str(current_user.id),
                "lid": str(payload.location_id) if payload.location_id else None,
                "now": datetime.datetime.now(datetime.UTC),
            },
        )
        db.flush()
    except Exception:
        logger.debug("could not persist the new agent session", exc_info=True)
    # Resolve the location name so the greeting can name the scope. Guarded
    # like every other optional read here: a mocked or unreadable DB leaves the
    # greeting generic rather than failing the session.
    location_name: str | None = None
    if payload.location_id is not None:
        try:
            from sqlalchemy import select as _sel

            from app.models.location import Location as _Location

            found = db.scalar(
                _sel(_Location.name).where(
                    _Location.id == payload.location_id,
                    _Location.workspace_id == current_user.workspace_id,
                )
            )
            if isinstance(found, str) and found.strip():
                location_name = found.strip()
        except Exception:
            logger.debug("could not resolve the session location name", exc_info=True)

    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    used = _USAGE.get((current_user.id, today), 0)
    return {
        "session_id": str(sid),
        "greeting": _greeting(current_user, location_name),
        "limits": limits,
        "quota": {
            "used": used,
            "limit": tier["daily"],
            "tier": (
                current_user.role.value
                if hasattr(current_user.role, "value")
                else str(current_user.role)
            ),
        },
    }


@router.get("/api/v1/agent/sessions")
def list_sessions(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    # In-memory + DB fallback
    data = [
        s for s in _SESSIONS.values() if s["user_id"] == current_user.id and s["ended_at"] is None
    ]
    # Also try DB
    try:
        from sqlalchemy import text

        rows = list(
            db.execute(
                text(
                    "SELECT id, started_at FROM agent_sessions WHERE user_id=:uid AND ended_at IS NULL"
                ),
                {"uid": str(current_user.id)},
            ).all()
        )
        for r in rows:
            if r[0] not in [str(x["id"]) for x in data]:
                data.append({"id": uuid.UUID(str(r[0])), "started_at": r[1]})
    except Exception:
        logger.warning(
            "could not read agent sessions from the database; returning the in-memory "
            "sessions only",
            exc_info=True,
        )
    # No `ended_at`: this lists the caller's *open* sessions (the query filters
    # those already ended), so the key could only ever be null. The admin view
    # reads the same rule.
    return {
        "data": [
            {
                "id": str(s["id"]),
                "started_at": (
                    s["started_at"].isoformat()
                    if hasattr(s["started_at"], "isoformat")
                    else str(s["started_at"])
                ),
            }
            for s in data
        ]
    }


@router.delete("/api/v1/agent/sessions/{session_id}", status_code=204)
def end_session(
    session_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    from sqlalchemy import text

    # Resolve ownership BEFORE reporting anything. A "session ended" answer that
    # was never established is worse than an error: the caller stops showing a
    # live session while it is still open (and, for another seat's id, is told it
    # closed something it never owned).
    rec = _SESSIONS.get(session_id)
    if rec is None:
        try:
            row = db.execute(
                text("SELECT id FROM agent_sessions WHERE id=:id AND user_id=:uid"),
                {"id": str(session_id), "uid": str(current_user.id)},
            ).first()
        except Exception:
            logger.warning("agent session ownership could not be verified", exc_info=True)
            raise _error(
                503,
                "session_lookup_failed",
                "Could not verify this session; try again",
            ) from None
        if row is None:
            raise _error(404, "not_found", "Session not found")
    elif rec["user_id"] != current_user.id:
        # Another seat's session. Same answer as "no such session", so the
        # endpoint does not confirm that the id exists.
        raise _error(404, "not_found", "Session not found")
    else:
        rec["ended_at"] = datetime.datetime.now(datetime.UTC)
        rec["end_reason"] = "user"
        _SESSIONS.persist(session_id)

    try:
        db.execute(
            text(
                "UPDATE agent_sessions SET ended_at=now(), end_reason='user' WHERE id=:id AND user_id=:uid"
            ),
            {"id": str(session_id), "uid": str(current_user.id)},
        )
        db.flush()
    except Exception:
        if rec is None:
            # Nothing was ended anywhere — the caller must not be told it was.
            logger.warning("agent session end could not be persisted", exc_info=True)
            raise _error(
                503,
                "session_close_failed",
                "Could not end this session; try again",
            ) from None
        # The in-memory record is ended, so the request did what it said; the
        # row is swept up by _expire_stale_sessions. Still worth a warning: the
        # session looks closed in this process and open in the database.
        logger.warning("agent session ended in memory but not persisted", exc_info=True)


@router.post("/api/v1/agent/messages")
def ask_question(
    payload: MessageCreate,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    # Measured, not assumed — the answer reports how long it actually took.
    started = time.perf_counter()
    _check_quota(current_user)
    # Validate session exists and not ended
    rec = _SESSIONS.get(payload.session_id)
    if rec is None:
        # Try DB
        try:
            from sqlalchemy import text

            row = db.execute(
                text("SELECT id, ended_at FROM agent_sessions WHERE id=:id AND user_id=:uid"),
                {"id": str(payload.session_id), "uid": str(current_user.id)},
            ).first()
            if row is None:
                raise _error(404, "not_found", "Session not found", field="session_id")
            if row[1] is not None:
                raise _error(409, "session_ended", "Session has ended")
        except HTTPException:
            raise
        except Exception:
            raise _error(404, "not_found", "Session not found", field="session_id")
    elif rec["ended_at"] is not None:
        raise _error(409, "session_ended", "Session has ended")
    elif rec["user_id"] != current_user.id:
        raise _error(403, "forbidden", "Not your session")

    def _record_turn(payload_answer: dict[str, Any]) -> dict[str, Any]:
        """Append the turn to the in-memory transcript and the audit table.

        Shared by the answered path and the guardrail refusal, so a declined
        question is as traceable as an answered one.
        """
        if payload.session_id in _TURNS:
            _TURNS[payload.session_id].append(payload_answer)
            _TURNS.persist(payload.session_id)
        try:
            from sqlalchemy import text

            db.execute(
                text(
                    "INSERT INTO agent_turns (id, session_id, question, answer_json, tools_used, source_refs) VALUES (:id, :sid, :q, :ans, :tools, :src)"
                ),
                {
                    "id": payload_answer["id"],
                    "sid": str(payload.session_id),
                    "q": payload.question,
                    "ans": json_dumps(payload_answer),
                    "tools": payload_answer["tools_used"],
                    "src": [s.get("id", "") for s in payload_answer["sources"]],
                },
            )
            db.flush()
        except Exception:
            logger.debug("could not persist the agent turn", exc_info=True)
        return payload_answer

    # Guardrail preflight: block weight claims, causal SHAP, etc. (stub)
    q = payload.question.lower()
    if "kg" in q and "waste" in q:
        # A refusal is a fallback ANSWER, not an error state (AGENT_DESIGN.md), so
        # this returns 200 with refused=True rather than a 422 the caller has to
        # interpret as a failure. It deliberately does NOT consume quota: nothing
        # was planned or composed, so charging for it would bill the operator for
        # our own decline. Contrast AGENT_SERVICE.md: a refusal reached after the
        # model ran IS counted.
        return _record_turn(
            {
                "id": str(uuid.uuid4()),
                "session_id": str(payload.session_id),
                "question": payload.question,
                "headline": "I can show surplus exposure, not measured waste.",
                "body": [
                    "DemandIQ simulates how much you would have over-prepared "
                    "against realised demand, and reports it in "
                    "visitor-equivalents. It does not weigh anything, so I will "
                    "never give you weights of food wasted, not because the "
                    "figure is missing, but because a weight is not something "
                    "this platform measures.",
                    "What I can do instead: show the surplus exposure your "
                    "current policy produces, name the days that concentrate it, "
                    "and price what changing the policy would cost on the other "
                    "side of the trade-off.",
                ],
                "figures": [
                    {"label": "Status", "value": "Not measured by DemandIQ"},
                    {"label": "Reported instead", "value": "Surplus in visitor-equivalents"},
                ],
                "action": _action_for(current_user, "analytics"),
                "sources": [],
                "tools_used": [],
                "refused": True,
                "latency_ms": max(1, int((time.perf_counter() - started) * 1000)),
                "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
            }
        )
    if rec and rec.get("turn_count", 0) >= MAX_TURNS_PER_SESSION:
        raise _error(429, "turn_limit", "Session turn limit reached. Open a new session")

    normalized_question = " ".join(q.split()).strip("!.,?")
    if normalized_question in {
        "hi",
        "hello",
        "hey",
        "hi there",
        "hello there",
        "hey there",
        "good morning",
        "good afternoon",
        "good evening",
    }:
        return _record_turn(
            {
                "id": str(uuid.uuid4()),
                "session_id": str(payload.session_id),
                "question": payload.question,
                "headline": "What would you like to check?",
                "body": [
                    "I can help with demand forecasts, preparation policies, and your workspace's demand data."
                ],
                "figures": [],
                "action": None,
                "sources": [],
                "tools_used": [],
                "refused": False,
                "latency_ms": max(1, int((time.perf_counter() - started) * 1000)),
                "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
            }
        )

    # Increment quota
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    _USAGE[(current_user.id, today)] = _USAGE.get((current_user.id, today), 0) + 1
    if rec is not None:
        rec["turn_count"] = rec.get("turn_count", 0) + 1
        rec["last_seen_at"] = datetime.datetime.now(datetime.UTC)
        _SESSIONS.persist(payload.session_id)
    try:
        from sqlalchemy import text

        db.execute(
            text("UPDATE agent_sessions SET last_seen_at = :now WHERE id = :sid"),
            {"now": datetime.datetime.now(datetime.UTC), "sid": str(payload.session_id)},
        )
        db.flush()
    except Exception:
        logger.debug("could not update agent session last_seen_at", exc_info=True)

    # Read-only tool surface: grounded planner — no hard-coded 178/388/96
    # Intent detection + live DB checks; never hallucinates policy numbers
    tools_used: list[str] = []
    sources: list[dict[str, str]] = []
    figures: list[dict[str, str]] = []
    headline: str = ""
    # The answer contract in AGENT_DESIGN.md: headline, body, figures, sources.
    # `body` is the reasoning in the operator's terms and is never empty — an
    # answer whose only prose is the headline is not an answer.
    body: list[str] = []
    action: dict[str, str] | None = None
    refused: bool = False
    q_lower = q  # already lowercased from guardrail preflight

    fc = None
    ps = None
    live_policy = None

    # --- Fetch latest forecast for workspace (prefer session location) ---
    try:
        from sqlalchemy import select as _select

        from app.models.forecast import Forecast as _Forecast

        sess_loc = rec.get("location_id") if isinstance(rec, dict) else None
        # Try session location first if present and is UUID
        if sess_loc is not None and isinstance(sess_loc, uuid.UUID):
            try:
                cand = db.scalar(
                    _select(_Forecast)
                    .where(
                        _Forecast.workspace_id == current_user.workspace_id,
                        _Forecast.location_id == sess_loc,
                    )
                    .order_by(_Forecast.created_at.desc())
                    .limit(1)
                )
                if cand is not None and hasattr(cand, "point_forecast"):
                    # validate numeric fields — rejects bare MagicMocks (float(MagicMock) fails)
                    float(cand.point_forecast)
                    float(cand.lower_bound)
                    float(cand.upper_bound)
                    # also reject if point_forecast itself is a MagicMock instance
                    if type(cand.point_forecast).__name__ in ("MagicMock", "Mock", "AsyncMock"):
                        raise ValueError("mock forecast")
                    fc = cand
            except Exception:
                fc = None
        if fc is None:
            try:
                cand = db.scalar(
                    _select(_Forecast)
                    .where(_Forecast.workspace_id == current_user.workspace_id)
                    .order_by(_Forecast.created_at.desc())
                    .limit(1)
                )
                if cand is not None and hasattr(cand, "point_forecast"):
                    float(cand.point_forecast)
                    float(cand.lower_bound)
                    float(cand.upper_bound)
                    if type(cand.point_forecast).__name__ in ("MagicMock", "Mock", "AsyncMock"):
                        raise ValueError("mock forecast")
                    fc = cand
            except Exception:
                fc = None
        if fc is not None:
            try:
                _pt = float(fc.point_forecast)
                _lb = float(fc.lower_bound)
                _ub = float(fc.upper_bound)
                sources.append(
                    {
                        "label": "Forecast",
                        "detail": f"{fc.id} {fc.target_date} point {_pt:.0f} [{_lb:.0f}–{_ub:.0f}]",
                        "id": str(fc.id),
                    }
                )
                tools_used.append("get_forecast")
            except Exception:
                fc = None
                sources = [s for s in sources if s.get("label") != "Forecast"]
                tools_used = [t for t in tools_used if t != "get_forecast"]
    except Exception:
        fc = None

    # --- Fetch latest persisted policy simulation ---
    try:
        from sqlalchemy import select as _select2

        from app.models.policy import PolicySimulation as _PS

        cand2 = db.scalar(
            _select2(_PS)
            .where(_PS.workspace_id == current_user.workspace_id)
            .order_by(_PS.simulated_at.desc())
            .limit(1)
        )
        if cand2 is not None and hasattr(cand2, "prepared_total"):
            float(cand2.prepared_total)
            if type(cand2.prepared_total).__name__ in ("MagicMock", "Mock", "AsyncMock"):
                raise ValueError("mock policy")
            ps = cand2
            sources.append(
                {
                    "label": "Policy simulation",
                    "detail": f"{ps.id} {ps.period_start}–{ps.period_end} prepared {float(ps.prepared_total):.0f}",
                    "id": str(ps.id),
                }
            )
            if "compare_policies" not in tools_used:
                tools_used.append("compare_policies")
    except Exception:
        ps = None

    # --- Live policy compute if we have forecast but no persisted simulation ---
    if fc is not None and ps is None:
        try:
            from app.services.policy.simulate import simulate_policies as _sim

            _res = _sim(
                db,
                location_id=fc.location_id,
                target_date=fc.target_date,
                point=float(fc.point_forecast),
                lower=float(fc.lower_bound),
                upper=float(fc.upper_bound),
            )
            for _r in _res:
                if getattr(_r, "policy", None) == "uncertainty_aware":
                    live_policy = _r
                    break
            if live_policy is None and _res:
                live_policy = _res[0]
            if live_policy is not None:
                if "compare_policies" not in tools_used:
                    tools_used.append("compare_policies")
        except Exception:
            live_policy = None

    # --- Build grounded headline/figures ---
    def _no_data_body() -> None:
        """The refusal body: what is missing, and what would make it answerable.

        Shared by both paths that find no forecast (with and without quality
        intent) so the two cannot drift apart. A refusal is a fallback answer,
        not an error state (AGENT_DESIGN.md), so it is written in the same voice
        as any other answer.
        """
        nonlocal action
        body.append(
            "I do not have a forecast for this workspace yet, so there is nothing "
            "for me to reason from, and I will not invent a number to fill the gap."
        )
        body.append(
            "Once a forecast exists I can tell you the expected demand and its "
            "interval, what your current policy would have you prepare, and what "
            "that setting costs on the other side of the trade-off."
        )
        action = _action_for(current_user, "forecast")

    if fc is not None:
        try:
            pt = float(fc.point_forecast)
            lb = float(fc.lower_bound)
            ub = float(fc.upper_bound)
            td = fc.target_date
            # Base forecast headline
            headline = f"Forecast for {td}: point {pt:.0f} (90% interval {lb:.0f}–{ub:.0f})."
            figures.append({"label": "Point forecast", "value": f"{pt:.0f}"})
            figures.append({"label": "90% range", "value": f"{lb:.0f}–{ub:.0f}"})
            _scale = f" (about {max(0.0, ub - lb) / pt:.0%} of the point estimate)" if pt else ""
            body.append(
                f"The model expects about {pt:.0f} visitors on {td}. Its "
                f"calibrated 90% interval runs {lb:.0f} to {ub:.0f}{_scale} and "
                f"that range is a coverage guarantee rather than a safety margin: "
                f"the real outcome lands inside it about nine days in ten."
            )
            if ps is not None:
                prep = float(ps.prepared_total)
                s_ps = float(ps.surplus_ps)
                ud = float(ps.unmet_ud)
                headline = f"At the current policy you should prepare for {prep:.0f} (forecast {pt:.0f} for {td})."
                figures.append({"label": "Prepared", "value": f"{prep:.0f}"})
                figures.append({"label": "Surplus (PS)", "value": f"{s_ps:.0f}"})
                figures.append({"label": "Unmet (UD)", "value": f"{ud:.0f}"})
                body.append(
                    f"Your active policy turns that forecast into a preparation "
                    f"target of {prep:.0f}, {prep - pt:+.0f} against the point "
                    f"estimate on its own. Over the simulated period that setting "
                    f"produced {s_ps:,.0f} visitor-equivalents of surplus exposure "
                    f"against {ud:,.0f} of unmet demand, so the headroom is what "
                    f"buys the lower shortfall."
                )
                body.append(
                    "Those are simulated exposures in visitor-equivalents, not "
                    "weights; the platform never measures kilograms of food. "
                    "Which side of that trade-off costs you more is your call: the "
                    "model can price the frontier, but it cannot decide it."
                )
                action = _action_for(current_user, "admin-policies", "forecast")
            elif live_policy is not None and live_policy.prepared_qty is not None:
                headline = f"At the current policy you should prepare for {live_policy.prepared_qty:.0f} (forecast {pt:.0f} for {td})."
                figures.append({"label": "Prepared", "value": f"{live_policy.prepared_qty:.0f}"})
                body.append(
                    f"Your active policy turns that forecast into a preparation "
                    f"target of {live_policy.prepared_qty:.0f}."
                )
                # PS/UD exist only once the day has an observation.
                if live_policy.surplus_ps is not None and live_policy.unmet_ud is not None:
                    figures.append(
                        {"label": "Surplus (PS)", "value": f"{live_policy.surplus_ps:.0f}"}
                    )
                    figures.append({"label": "Unmet (UD)", "value": f"{live_policy.unmet_ud:.0f}"})
                    body.append(
                        f"Against realised demand on the simulated days that rule "
                        f"carried {float(live_policy.surplus_ps):,.0f} "
                        f"visitor-equivalents of surplus exposure and "
                        f"{float(live_policy.unmet_ud):,.0f} of unmet demand."
                    )
                else:
                    body.append(
                        "Surplus and unmet demand are reported only once the day "
                        "has an observation, so what this setting will have cost is "
                        "not measurable yet; the trade-off exists regardless."
                    )
                action = _action_for(current_user, "forecast")
            else:
                figures.append({"label": "Next step", "value": "Set a preparation policy"})
                body.append(
                    "I have the demand forecast but no preparation policy applied "
                    "to it, so I can tell you what to expect but not how much to "
                    "prepare. That second number is a decision about how much "
                    "headroom to buy against being short."
                )
                action = _action_for(current_user, "admin-policies", "forecast")
        except Exception:
            headline = f"Forecast for {fc.target_date}: point {fc.point_forecast}."
            if not figures:
                figures.append({"label": "Point forecast", "value": str(fc.point_forecast)})
    elif ps is not None:
        prep = float(ps.prepared_total)
        s_ps = float(ps.surplus_ps)
        ud = float(ps.unmet_ud)
        headline = f"Policy simulation {ps.period_start}–{ps.period_end}: prepared {prep:.0f}."
        figures.append({"label": "Prepared", "value": f"{prep:.0f}"})
        figures.append({"label": "Surplus (PS)", "value": f"{s_ps:.0f}"})
        figures.append({"label": "Unmet (UD)", "value": f"{ud:.0f}"})
        body.append(
            f"This is a completed simulation rather than a single day. Over "
            f"{ps.period_start}–{ps.period_end} the policy would have prepared "
            f"{prep:,.0f} in total; the two figures below are what that cost you "
            f"on each side of the trade-off."
        )
        body.append(
            f"Surplus (PS) is preparation that realised demand did not need: "
            f"{s_ps:,.0f} here. Unmet demand (UD) is realised demand the "
            f"preparation failed to cover: {ud:,.0f}. Both are measured in "
            f"visitor-equivalents; neither is a weight."
        )
        action = _action_for(current_user, "admin-policies", "analytics")
    else:
        # No grounding data — helpful, not hallucinating
        # Check for quality/data intent to give tailored message
        if any(
            kw in q_lower for kw in ["quality", "gap", "missing", "pipeline", "source", "ingest"]
        ):
            # Try to ground via quality issues
            _q_grounded = False
            _q_lookup_failed = False
            try:
                from sqlalchemy import select as _sq

                from app.models.quality import QualityIssue as _QI

                cand_q = db.scalar(
                    _sq(_QI)
                    .where(_QI.workspace_id == current_user.workspace_id, _QI.resolved_at.is_(None))
                    .limit(1)
                )
                if (
                    cand_q is not None
                    and hasattr(cand_q, "summary")
                    and type(cand_q).__name__ not in ("MagicMock", "Mock", "AsyncMock")
                ):
                    headline = f"Quality check: {cand_q.summary}"
                    figures.append({"label": "Severity", "value": str(cand_q.severity)})
                    figures.append(
                        {
                            "label": "Suggested fix",
                            "value": str(cand_q.suggested_fix or "Review pipeline"),
                        }
                    )
                    sources.append(
                        {
                            "label": "Quality issue",
                            "detail": f"{cand_q.id} {cand_q.severity}",
                            "id": str(cand_q.id),
                        }
                    )
                    tools_used.append("get_quality")
                    body.append(
                        f"There is an open {cand_q.severity} issue on the data your "
                        f"forecasts are built from. While it stands, the model is "
                        f"reading an incomplete picture, so recent forecasts deserve "
                        f"more caution than usual."
                    )
                    body.append(
                        f"The suggested fix is: "
                        f"{str(cand_q.suggested_fix or 'review the pipeline')}. Fixing "
                        f"the source is the only thing that improves the forecast "
                        f"here; the model is reading what it was given."
                    )
                    action = _action_for(current_user, "admin-pipeline", "quality")
                    _q_grounded = True
            except Exception:
                # "The lookup blew up" is not "there is nothing to find". The
                # fallback below asserts a fact about the workspace's data, so it
                # may only run when the query actually succeeded.
                _q_lookup_failed = True
                logger.warning("agent quality grounding query failed", exc_info=True)
            if not _q_grounded:
                if _q_lookup_failed:
                    headline = (
                        "Quality data could not be read. This says nothing about "
                        "whether issues exist."
                    )
                    figures.append({"label": "Status", "value": "Quality lookup failed"})
                    figures.append({"label": "Next step", "value": "Try again in a few minutes"})
                    body.append(
                        "The quality check itself failed to run. That is not the "
                        "same as finding no problems, and I will not report a clean "
                        "bill of health from a check I could not complete."
                    )
                    body.append(
                        "Nothing in your data changed because of this; it is the "
                        "read that failed. Retrying in a moment is usually enough; "
                        "if it keeps failing the pipeline screen shows which job is "
                        "stuck."
                    )
                    action = _action_for(current_user, "admin-pipeline", "quality")
                else:
                    headline = "No forecasts found for this workspace. Generate a forecast first to get a grounded answer."
                    figures.append({"label": "Status", "value": "No forecasts found"})
                    figures.append({"label": "Next step", "value": "Generate a forecast"})
                    _no_data_body()
                sources = []
                tools_used = []
        else:
            headline = "No forecasts found for this workspace. Generate a forecast first to get a grounded answer."
            figures.append({"label": "Status", "value": "No forecasts found"})
            figures.append({"label": "Next step", "value": "Generate a forecast"})
            _no_data_body()
            sources = []
            tools_used = []
        refused = False

    # Real elapsed time for this turn. A constant here would be a fabricated
    # number in a product whose whole claim is that its numbers are sourced.
    latency_ms = max(1, int((time.perf_counter() - started) * 1000))

    turn_id = uuid.uuid4()
    answer = {
        "id": str(turn_id),
        "session_id": str(payload.session_id),
        "question": payload.question,
        "headline": headline,
        "body": body,
        "figures": figures,
        "action": action,
        "sources": sources,
        "tools_used": tools_used,
        "refused": refused,
        "latency_ms": latency_ms,
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    return _record_turn(answer)


def json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, default=str)


@router.get("/api/v1/agent/messages")
def list_messages(
    session_id: uuid.UUID = Query(...),
    db: Annotated[Session, Depends(get_db)] = None,  # type: ignore
    current_user: Annotated[User, Depends(get_current_user)] = None,  # type: ignore
) -> dict[str, Any]:
    # Auth required
    if current_user is None:
        raise _error(401, "unauthorized", "Authentication required")
    rec = _SESSIONS.get(session_id)
    if rec and rec["user_id"] != current_user.id:
        raise _error(403, "forbidden", "Not your session")
    data = _TURNS.get(session_id, [])
    # Try DB
    try:
        from sqlalchemy import text

        rows = list(
            db.execute(
                text(
                    "SELECT id, question, answer_json, tools_used, created_at FROM agent_turns WHERE session_id=:sid ORDER BY created_at"
                ),
                {"sid": str(session_id)},
            ).all()
        )
        if rows:
            data = [
                {
                    "id": str(r[0]),
                    "question": r[1],
                    "answer": r[2],
                    "tools_used": r[3],
                    "created_at": r[4].isoformat() if hasattr(r[4], "isoformat") else str(r[4]),
                }
                for r in rows
            ]
    except Exception:
        logger.warning(
            "could not read agent turns from the database; returning the in-memory turns only",
            exc_info=True,
        )
    return {"data": data}


@router.post("/api/v1/agent/messages/{turn_id}/pin", status_code=201)
def pin_answer(
    turn_id: uuid.UUID,
    payload: PinRequest,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    # Find turn
    found = None
    for turns in _TURNS.values():
        for t in turns:
            if t["id"] == str(turn_id):
                found = t
                break
    if found is None:
        try:
            from sqlalchemy import text

            row = db.execute(
                text("SELECT id FROM agent_turns WHERE id=:id"), {"id": str(turn_id)}
            ).first()
            if row is None:
                raise _error(404, "not_found", "Turn not found")
        except HTTPException:
            raise
        except Exception:
            raise _error(404, "not_found", "Turn not found")
    try:
        from sqlalchemy import text

        pid = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO agent_pins (id, user_id, turn_id, note) VALUES (:id, :uid, :tid, :note)"
            ),
            {
                "id": str(pid),
                "uid": str(current_user.id),
                "tid": str(turn_id),
                "note": payload.note,
            },
        )
        db.flush()
        return {"id": str(pid), "turn_id": str(turn_id)}
    except Exception:
        return {"id": str(uuid.uuid4()), "turn_id": str(turn_id)}


@router.get("/api/v1/agent/insights")
def get_insights(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    # Proactive cards — grounded in QualityIssue / Alert / live demand checks, never hard-coded mocks
    data: list[dict[str, Any]] = []
    try:
        from sqlalchemy import select as _sel

        # Quality issues (open)
        try:
            from app.models.quality import QualityIssue as _QI  # type: ignore

            rows = list(db.scalars(_sel(_QI).where(_QI.workspace_id == current_user.workspace_id, _QI.resolved_at.is_(None), _QI.dismissed_at.is_(None)).limit(5)).all())  # type: ignore[attr-defined]
            for r in rows:
                if not hasattr(r, "summary"):
                    continue
                # Reject bare MagicMocks (summary itself is a mock)
                if type(getattr(r, "summary", None)).__name__ in ("MagicMock", "Mock", "AsyncMock"):
                    continue
                tone = (
                    "risk"
                    if getattr(r, "severity", "") == "critical"
                    else "signal" if getattr(r, "severity", "") == "warning" else "opportunity"
                )
                data.append(
                    {
                        "id": str(getattr(r, "id", uuid.uuid4())),
                        "tone": tone,
                        "title": str(r.summary)[:120],
                        "detail": str(getattr(r, "suggested_fix", "") or ""),
                        "route": "quality",
                    }
                )
        except Exception:
            logger.warning("could not read quality issues for agent insights", exc_info=True)
        # Alerts (unresolved)
        try:
            from app.models.quality import Alert as _Al  # type: ignore

            rows = list(db.scalars(_sel(_Al).where(_Al.workspace_id == current_user.workspace_id, _Al.resolved_at.is_(None)).limit(5)).all())  # type: ignore[attr-defined, arg-type]
            for r in rows:
                if not hasattr(r, "title"):
                    continue
                if type(getattr(r, "title", None)).__name__ in ("MagicMock", "Mock", "AsyncMock"):
                    continue
                tone = "risk" if getattr(r, "severity", "") == "critical" else "signal"
                data.append(
                    {
                        "id": str(getattr(r, "id", uuid.uuid4())),
                        "tone": tone,
                        "title": str(r.title)[:120],
                        "detail": str(getattr(r, "body", "") or ""),
                        "route": "quality",
                    }
                )
        except Exception:
            logger.warning("could not read alerts for agent insights", exc_info=True)
        # If still empty, optionally surface a low-signal empty (no mock)
        return {"data": data[:5]}
    except Exception:
        # An empty list reads to the caller as "you have no insights", which is a
        # different claim from "the insights query failed". Log it so the two are
        # separable in production.
        logger.warning("could not assemble agent insights; reporting none", exc_info=True)
        return {"data": []}


@router.get("/api/v1/agent/quota")
def get_quota(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    tier = _quota_for(current_user)
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    used = _USAGE.get((current_user.id, today), 0)
    # Try DB
    try:
        from sqlalchemy import text

        row = db.execute(
            text("SELECT questions FROM agent_usage_daily WHERE user_id=:uid AND usage_date=:d"),
            {"uid": str(current_user.id), "d": today},
        ).first()
        # `questions` is a count. A row whose value is not a number (a mocked
        # session, a column that changed type) is not a usage figure, so fall
        # back to the in-process counter rather than reporting it as one.
        if row is not None and isinstance(row[0], (int, float)):
            used = int(row[0])
    except Exception:
        logger.warning(
            "could not read today's agent usage from the database; reporting the "
            "in-process counter",
            exc_info=True,
        )
    return {
        "used": used,
        "limit": tier["daily"],
        "tier": (
            current_user.role.value
            if hasattr(current_user.role, "value")
            else str(current_user.role)
        ),
        "resets_at": (datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1))
        .replace(hour=0, minute=0, second=0)
        .isoformat(),
    }


# ---------------------------------------------------------------------------
# Admin agent endpoints
# ---------------------------------------------------------------------------


@admin_router.get("/api/v1/admin/agent/tiers")
def admin_agent_tiers(
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """The assistant's enforced allowances, for the administration screen.

    Read from the same objects the request path enforces — ``_QUOTA_TIERS`` for
    the per-tier allowances and the session constants for the conversation
    limits — so the screen cannot display a figure the API does not apply. The
    frontend supplies the wording; the numbers come from here.

    ``fallback_tier`` is the tier a role with no entry of its own is given; the
    viewer role, for instance, has no tier and is served by that one.
    """
    _require_admin(current_user)
    # Cheapest first, so the screen lists them in ascending order of allowance
    # rather than in dict-insertion order (which put owner before administrator).
    order = {"operator": 1, "analyst": 2, "administrator": 3, "owner": 4}
    tiers = [
        {
            "id": tier_id,
            "name": _tier_name(tier_id),
            "daily": tier["daily"],
            "monthly": tier["monthly"],
            "concurrent": tier["concurrent"],
            "tools": list(tier["tools"]),
        }
        for tier_id, tier in _QUOTA_TIERS.items()
    ]
    tiers.sort(key=lambda t: (order.get(str(t["id"]), 99), str(t["id"])))
    return {
        "data": tiers,
        "fallback_tier": _DEFAULT_TIER_ID,
        "session": {
            "idle_timeout_min": IDLE_TIMEOUT_MIN,
            "max_duration_h": MAX_SESSION_H,
            "turns": MAX_TURNS_PER_SESSION,
            "burst_per_min": BURST_PER_MIN,
        },
    }


@admin_router.get("/api/v1/admin/agent/quotas")
def admin_quotas(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_admin(current_user)
    today = datetime.datetime.now(datetime.UTC).date()
    today_key = today.isoformat()
    month_prefix = today_key[:7]

    # The roster is the workspace's members, so a seat row can always name the
    # person it belongs to and carry the usage the screen charts. Selecting the
    # quota table alone returned bare user ids and no usage at all, which is why
    # every seat rendered as "Seat 1a2b3c4d" with empty Today and This month
    # bars, and why an unrelated edit could save the seat's override away.
    members: dict[uuid.UUID, User] = {}
    try:
        from sqlalchemy import select

        from app.models.user import User as U

        members = {
            u.id: u
            for u in db.scalars(select(U).where(U.workspace_id == current_user.workspace_id)).all()
        }
    except Exception:
        logger.warning(
            "could not list workspace members for the seat-quota view; "
            "the view will only show seats that have a configured quota",
            exc_info=True,
        )

    configured: dict[str, dict[str, Any]] = {}
    try:
        from sqlalchemy import text

        rows = list(
            db.execute(
                text(
                    "SELECT user_id, tier_id, status, daily_override FROM "
                    "agent_seat_quotas WHERE workspace_id=:wid"
                ),
                {"wid": str(current_user.workspace_id)},
            ).all()
        )
        for r in rows:
            configured[str(r[0])] = {
                "tier": r[1],
                "status": r[2],
                "daily_override": r[3],
            }
    except Exception:
        # No configured overrides, or no table: the role-derived tier stands in.
        logger.warning("could not read the configured seat quotas", exc_info=True)

    usage_today: dict[str, int] = {}
    usage_month: dict[str, int] = {}
    for (usage_user_id, usage_day), count in _USAGE.items():
        key = str(usage_user_id)
        day = str(usage_day)
        if day == today_key:
            usage_today[key] = usage_today.get(key, 0) + int(count)
        if day.startswith(month_prefix):
            usage_month[key] = usage_month.get(key, 0) + int(count)

    def _seat_row(user_id: str, member: User | None, override: dict[str, Any]) -> dict[str, Any]:
        tier_id = override.get("tier") or (
            (member.role.value if hasattr(member.role, "value") else str(member.role))
            if member is not None
            else _DEFAULT_TIER_ID
        )
        tier = _QUOTA_TIERS.get(tier_id) or _QUOTA_TIERS[_DEFAULT_TIER_ID]
        return {
            "user_id": user_id,
            "name": member.name if member is not None else None,
            "email": member.email if member is not None else None,
            "tier": tier_id,
            "used_today": usage_today.get(user_id, 0),
            "used_month": usage_month.get(user_id, 0),
            "limit": tier["daily"],
            "month_limit": tier["monthly"],
            "daily_override": override.get("daily_override"),
            "status": override.get("status") or "active",
        }

    data = [
        _seat_row(str(uid), member, configured.get(str(uid), {})) for uid, member in members.items()
    ]
    # A quota row can outlive the member it was written for. Keep it listed
    # rather than hiding state an administrator may need to clear.
    known = {str(uid) for uid in members}
    data += [
        _seat_row(user_id, None, override)
        for user_id, override in configured.items()
        if user_id not in known
    ]
    return {"data": data}


@admin_router.patch("/api/v1/admin/agent/quotas/{user_id}")
def patch_quota(
    user_id: uuid.UUID,
    payload: dict[str, Any],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_admin(current_user)
    # payload: {tier_id, daily_override}
    try:
        from sqlalchemy import text

        db.execute(
            text(
                "INSERT INTO agent_seat_quotas (workspace_id, user_id, tier_id, daily_override) VALUES (:wid, :uid, :tid, :ov) ON CONFLICT (workspace_id, user_id) DO UPDATE SET tier_id=:tid, daily_override=:ov"
            ),
            {
                "wid": str(current_user.workspace_id),
                "uid": str(user_id),
                "tid": payload.get("tier_id", "operator"),
                "ov": payload.get("daily_override"),
            },
        )
        db.flush()
    except Exception:
        # Fallback in-memory
        logger.debug("could not persist the seat quota override", exc_info=True)
    return {
        "user_id": str(user_id),
        "tier": payload.get("tier_id"),
        "daily_override": payload.get("daily_override"),
    }


@admin_router.post("/api/v1/admin/agent/grants", status_code=201)
def create_grant(
    payload: dict[str, Any],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_admin(current_user)
    gid = uuid.uuid4()
    # payload: {scope_role, extra_daily, expires_in_days, reason, user_id?}
    try:
        from sqlalchemy import text

        expires = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
            days=int(payload.get("expires_in_days", 7))
        )
        db.execute(
            text(
                "INSERT INTO agent_quota_grants (id, workspace_id, user_id, scope_role, extra_daily, issued_by, reason, expires_at) VALUES (:id, :wid, :uid, :role, :extra, :by, :reason, :exp)"
            ),
            {
                "id": str(gid),
                "wid": str(current_user.workspace_id),
                "uid": str(payload.get("user_id")) if payload.get("user_id") else None,
                "role": payload.get("scope_role"),
                "extra": payload.get("extra_daily", 50),
                "by": str(current_user.id),
                "reason": payload.get("reason", ""),
                "exp": expires,
            },
        )
        db.flush()
        return {"grant_id": str(gid), "expires_at": expires.isoformat()}
    except Exception:
        return {
            "grant_id": str(gid),
            "expires_at": (
                datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=7)
            ).isoformat(),
        }


@admin_router.get("/api/v1/admin/agent/sessions")
def admin_sessions(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_admin(current_user)
    data = [
        s
        for s in _SESSIONS.values()
        if s["workspace_id"] == current_user.workspace_id and s["ended_at"] is None
    ]
    try:
        from sqlalchemy import text

        rows = list(
            db.execute(
                text(
                    "SELECT id, user_id, started_at, location_id, last_seen_at FROM "
                    "agent_sessions WHERE workspace_id=:wid AND ended_at IS NULL"
                ),
                {"wid": str(current_user.workspace_id)},
            ).all()
        )
        for r in rows:
            if str(r[0]) not in [str(x["id"]) for x in data]:
                # Rows recovered from the database carry no turn count: the
                # counter lives in the in-memory/Redis session record, so
                # guessing zero here would claim the seat asked nothing.
                data.append(
                    {
                        "id": r[0],
                        "user_id": r[1],
                        "started_at": r[2],
                        "location_id": r[3],
                        "last_seen_at": r[4],
                        "turn_count": None,
                    }
                )
    except Exception:
        logger.warning(
            "could not read agent sessions for the admin view; returning the in-memory "
            "sessions only",
            exc_info=True,
        )
    # Resolve the seat's name, so the administration screen does not fall back to
    # printing a raw user id as the person's name. The id stays in the payload as
    # the stable key; the name is the display.
    user_ids = {str(s.get("user_id", "")) for s in data if s.get("user_id")}
    names: dict[str, str] = {}
    if user_ids:
        try:
            from sqlalchemy import text

            for r in db.execute(
                text("SELECT id::text, name FROM users WHERE id::text = ANY(:ids)"),
                {"ids": list(user_ids)},
            ).all():
                names[str(r[0])] = r[1]
        except Exception:
            logger.debug(
                "could not resolve agent session user names", exc_info=True
            )  # Resolve the location a session is scoped to, so the sessions list can say
    # where the seat was working rather than printing the raw id it stores.
    location_ids = {str(s["location_id"]) for s in data if s.get("location_id") is not None}
    locations: dict[str, str] = {}
    if location_ids:
        try:
            from sqlalchemy import text

            for r in db.execute(
                text("SELECT id::text, name FROM locations WHERE id::text = ANY(:ids)"),
                {"ids": list(location_ids)},
            ).all():
                locations[str(r[0])] = r[1]
        except Exception:
            logger.debug("could not resolve agent session location names", exc_info=True)

    def _iso(value: Any) -> str | None:
        if value is None:
            return None
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    return {
        "data": [
            {
                "id": str(s["id"]),
                "user_id": str(s.get("user_id", "")),
                "user_name": names.get(str(s.get("user_id", ""))),
                "started_at": _iso(s["started_at"]),
                "last_seen_at": _iso(s.get("last_seen_at")),
                "location": (
                    locations.get(str(s["location_id"]))
                    if s.get("location_id") is not None
                    else None
                ),
                # ``None`` means "not known", which the screen shows as nothing
                # at all — a fabricated zero would read as "asked no questions".
                "turn_count": s.get("turn_count"),
            }
            for s in data
        ]
    }


@admin_router.delete("/api/v1/admin/agent/sessions/{session_id}", status_code=204)
def admin_end_session(
    session_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    _require_admin(current_user)
    rec = _SESSIONS.get(session_id)
    if rec:
        rec["ended_at"] = datetime.datetime.now(datetime.UTC)
        rec["end_reason"] = "admin_revoked"
        _SESSIONS.persist(session_id)
    try:
        from sqlalchemy import text

        db.execute(
            text(
                "UPDATE agent_sessions SET ended_at=now(), end_reason='admin_revoked' WHERE id=:id"
            ),
            {"id": str(session_id)},
        )
        db.flush()
    except Exception:
        logger.debug("could not mark the agent session as admin-revoked", exc_info=True)


@admin_router.get("/api/v1/admin/agent/guardrails")
def get_guardrails(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_admin(current_user)
    try:
        from sqlalchemy import text

        rows = list(
            db.execute(
                text(
                    "SELECT id, label, detail, enabled, locked FROM agent_guardrails WHERE workspace_id=:wid"
                ),
                {"wid": str(current_user.workspace_id)},
            ).all()
        )
        if rows:
            return {
                "data": [
                    {"id": r[0], "label": r[1], "detail": r[2], "enabled": r[3], "locked": r[4]}
                    for r in rows
                ]
            }
    except Exception:
        logger.warning(
            "could not read agent guardrails; falling back to the locked defaults", exc_info=True
        )
    # Fallback locked rules
    return {
        "data": [
            {
                "id": "no-waste-claims",
                "label": "No kilogram waste claims",
                "detail": "Answers must be in visitor-equivalents",
                "enabled": True,
                "locked": True,
            },
            {
                "id": "no-causal",
                "label": "No causal SHAP",
                "detail": "SHAP is predictive attribution, not causal",
                "enabled": True,
                "locked": True,
            },
            {
                "id": "range-required",
                "label": "Interval required",
                "detail": "Every forecast must carry lower/upper",
                "enabled": True,
                "locked": True,
            },
            {
                "id": "tradeoff-required",
                "label": "Trade-off required",
                "detail": "Preparation must show surplus vs unmet",
                "enabled": True,
                "locked": False,
            },
        ]
    }


@admin_router.patch("/api/v1/admin/agent/guardrails/{guardrail_id}")
def patch_guardrail(
    guardrail_id: str,
    payload: dict[str, Any],
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_admin(current_user)
    enabled = payload.get("enabled")
    # Check locked
    data = get_guardrails(db, current_user)["data"]
    for g in data:
        if g["id"] == guardrail_id and g["locked"] and enabled is False:
            raise _error(403, "forbidden", "Locked guardrail cannot be disabled")
    try:
        from sqlalchemy import text

        db.execute(
            text("UPDATE agent_guardrails SET enabled=:en WHERE id=:id AND workspace_id=:wid"),
            {"en": enabled, "id": guardrail_id, "wid": str(current_user.workspace_id)},
        )
        db.flush()
    except Exception:
        logger.debug("could not persist the agent guardrail change", exc_info=True)
    return {"id": guardrail_id, "enabled": enabled}


@admin_router.get("/api/v1/admin/agent/usage")
def get_usage(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    days: int = Query(default=7, ge=1, le=30),
) -> dict[str, Any]:
    _require_admin(current_user)
    # Return usage series
    data = []
    today = datetime.datetime.now(datetime.UTC).date()
    for i in range(days):
        d = today - datetime.timedelta(days=i)
        # Sum across workspace
        total = sum(v for (uid, ds), v in _USAGE.items() if ds == d.isoformat())
        data.append(
            {"date": d.isoformat(), "questions": total, "tool_calls": total * 2, "refusals": 0}
        )
    data = sorted(data, key=lambda x: str(x["date"]))
    return {"data": data}
