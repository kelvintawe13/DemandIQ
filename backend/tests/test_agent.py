"""Agent router contract tests — ``/api/v1/agent/*`` and ``/api/v1/admin/agent/*``.

The router keeps an in-memory store and mirrors it to the database, falling back
to the in-memory copy when the session is mocked. These tests use that fallback
to run without Postgres, and pin the contracts that were previously unfalsifiable
because the module had no tests at all:

* a failed quality lookup is not reported as "no forecasts found";
* ending a session reports success only once ownership is established;
* the quota tier, the 6/min burst and the concurrency limit are enforced.

The module-level stores are reset per test — quota and rate-limit state would
otherwise leak between them.
"""

from __future__ import annotations

import datetime
import logging
import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.database import get_db
from app.deps import get_current_user
from app.main import create_app
from app.models.user import User, UserRole
from app.models.workspace import Workspace
from app.routers import agent as agent_router

# ---------------------------------------------------------------- fixtures ---


def _workspace() -> Workspace:
    return Workspace(
        id=uuid.uuid4(),
        name="Test Co",
        business_type="cafe",
        country="RW",
        timezone="Africa/Kigali",
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _user(ws_id: uuid.UUID, role: UserRole = UserRole.operator) -> User:
    return User(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        name="Test User",
        email="test@example.com",
        password_hash="$2b$12$dummy",
        role=role,
        email_verified=True,
        created_at=datetime.datetime.now(datetime.UTC),
    )


@pytest.fixture(autouse=True)
def _clean_agent_state() -> None:
    """The router's stores are module globals; every test starts empty."""
    for store in (
        agent_router._SESSIONS,
        agent_router._TURNS,
        agent_router._USAGE,
        agent_router._BURST,
    ):
        store.clear()
    yield
    for store in (
        agent_router._SESSIONS,
        agent_router._TURNS,
        agent_router._USAGE,
        agent_router._BURST,
    ):
        store.clear()


def _client(user: User, db: MagicMock | None = None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db if db is not None else MagicMock()  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    return TestClient(app)


def _seed_session(
    user: User, *, ended: bool = False, location_id: uuid.UUID | None = None
) -> uuid.UUID:
    """Put a live session in the router's in-memory store."""
    sid = uuid.uuid4()
    now = datetime.datetime.now(datetime.UTC)
    agent_router._SESSIONS[sid] = {
        "id": sid,
        "workspace_id": user.workspace_id,
        "user_id": user.id,
        "location_id": location_id,
        "started_at": now,
        "last_seen_at": now,
        "ended_at": now if ended else None,
        "end_reason": "user" if ended else None,
        "turn_count": 0,
    }
    agent_router._TURNS[sid] = []
    return sid


class _Issue:
    """Stand-in for a ``QualityIssue`` row (kept off MagicMock on purpose)."""

    def __init__(self, summary: str, severity: str = "warning") -> None:
        self.id = uuid.uuid4()
        self.summary = summary
        self.severity = severity
        self.suggested_fix = "Backfill the missing deliveries"


class _Forecast:
    """Stand-in for a ``Forecast`` row with real numeric bounds.

    A MagicMock is rejected by the router's validation, which is the point: an
    answer must be composed from numbers, not from an object that merely has the
    right attribute names.
    """

    def __init__(
        self,
        point: float,
        low: float,
        high: float,
        on: str = "2017-03-01",
    ) -> None:
        self.id = uuid.uuid4()
        self.location_id = uuid.uuid4()
        self.target_date = on
        self.point_forecast = point
        self.lower_bound = low
        self.upper_bound = high


def _ask(client: TestClient, sid: uuid.UUID, question: str) -> dict:
    resp = client.post(
        "/api/v1/agent/messages", json={"session_id": str(sid), "question": question}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------- sessions ----


def test_create_session_returns_limits_and_quota() -> None:
    """The limits handed to the client are the same constants enforced elsewhere.

    Read from the router's own constants rather than restated here, so an edit to
    a limit cannot leave the client's payload fixed while the request path moves:
    the two are tied to one source.
    """
    ws = _workspace()
    user = _user(ws.id)
    resp = _client(user).post("/api/v1/agent/sessions", json={})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["limits"] == {
        "idle_timeout_min": agent_router.IDLE_TIMEOUT_MIN,
        "max_duration_h": agent_router.MAX_SESSION_H,
        "turns": agent_router.MAX_TURNS_PER_SESSION,
        "burst_per_min": agent_router.BURST_PER_MIN,
    }
    assert body["quota"]["tier"] == "operator"
    assert body["quota"]["limit"] == agent_router._QUOTA_TIERS["operator"]["daily"]
    assert body["quota"]["used"] == 0


def test_created_session_is_listed_and_can_be_ended() -> None:
    ws = _workspace()
    user = _user(ws.id)
    client = _client(user)
    sid = client.post("/api/v1/agent/sessions", json={}).json()["session_id"]

    listed = client.get("/api/v1/agent/sessions").json()["data"]
    assert [s["id"] for s in listed] == [sid]

    assert client.delete(f"/api/v1/agent/sessions/{sid}").status_code == 204
    assert client.get("/api/v1/agent/sessions").json()["data"] == []


def test_list_sessions_omits_ended_sessions() -> None:
    ws = _workspace()
    user = _user(ws.id)
    _seed_session(user, ended=True)
    live = _seed_session(user)
    data = _client(user).get("/api/v1/agent/sessions").json()["data"]
    assert [s["id"] for s in data] == [str(live)]


def test_create_session_fails_when_the_daily_quota_is_used_up() -> None:
    """The enforced daily allowance is the tier's ``daily`` — not a literal."""
    ws = _workspace()
    user = _user(ws.id)
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    daily = agent_router._QUOTA_TIERS["operator"]["daily"]

    # One short of the tier's allowance is still allowed; at the allowance it is not.
    agent_router._USAGE[(user.id, today)] = daily - 1
    assert _client(user).post("/api/v1/agent/sessions", json={}).status_code == 201

    agent_router._USAGE[(user.id, today)] = daily
    resp = _client(user).post("/api/v1/agent/sessions", json={})
    assert resp.status_code == 429, resp.text
    assert resp.json()["error"]["code"] == "quota_exhausted_daily"


def test_burst_limit_fires_once_the_enforced_rate_is_reached() -> None:
    """Burst enforcement tracks ``BURST_PER_MIN``, the constant the UI reports."""
    user = _user(uuid.uuid4())
    for _ in range(agent_router.BURST_PER_MIN):
        agent_router._check_quota(user)
    with pytest.raises(HTTPException) as exc:
        agent_router._check_quota(user)
    assert exc.value.status_code == 429
    assert exc.value.detail["error"]["code"] == "rate_limited"


def test_create_session_fails_at_the_concurrency_limit() -> None:
    """Concurrency is enforced at the tier's ``concurrent``, not a literal."""
    ws = _workspace()
    user = _user(ws.id)
    concurrent = agent_router._QUOTA_TIERS["operator"]["concurrent"]
    for _ in range(concurrent):
        _seed_session(user)

    resp = _client(user).post("/api/v1/agent/sessions", json={})
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "session_limit_reached"


# ----------------------------------------------------------- end session ----


def test_end_session_rejects_another_seats_session() -> None:
    """A session owned by someone else is never reported as ended."""
    ws = _workspace()
    owner = _user(ws.id)
    other = _user(ws.id)
    sid = _seed_session(other)

    resp = _client(owner).delete(f"/api/v1/agent/sessions/{sid}")
    assert resp.status_code == 404, resp.text
    # And it is still open for its actual owner.
    assert agent_router._SESSIONS[sid]["ended_at"] is None


def test_end_session_returns_404_for_an_unknown_session() -> None:
    ws = _workspace()
    user = _user(ws.id)
    db = MagicMock()
    db.execute.return_value.first.return_value = None

    resp = _client(user, db).delete(f"/api/v1/agent/sessions/{uuid.uuid4()}")
    assert resp.status_code == 404, resp.text


def test_end_session_reports_a_failed_ownership_lookup_instead_of_success(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Without an in-memory record the database has to confirm ownership.

    When that lookup fails, ownership was never established, so the endpoint must
    not answer 204: the caller would stop showing a session that is still open.
    """
    ws = _workspace()
    user = _user(ws.id)
    db = MagicMock()
    db.execute.side_effect = RuntimeError("database unavailable")

    with caplog.at_level(logging.WARNING, logger=agent_router.__name__):
        resp = _client(user, db).delete(f"/api/v1/agent/sessions/{uuid.uuid4()}")

    assert resp.status_code == 503, resp.text
    assert resp.json()["error"]["code"] == "session_lookup_failed"
    assert "ownership could not be verified" in caplog.text


def test_end_session_accepts_a_session_only_the_database_knows_about() -> None:
    ws = _workspace()
    user = _user(ws.id)
    sid = uuid.uuid4()
    db = MagicMock()
    db.execute.return_value.first.return_value = (str(sid),)

    resp = _client(user, db).delete(f"/api/v1/agent/sessions/{sid}")
    assert resp.status_code == 204, resp.text


# --------------------------------------------------------------- messages ----


def test_ask_question_requires_a_known_session() -> None:
    ws = _workspace()
    user = _user(ws.id)
    db = MagicMock()
    db.execute.side_effect = RuntimeError("database unavailable")

    resp = _client(user, db).post(
        "/api/v1/agent/messages", json={"session_id": str(uuid.uuid4()), "question": "hello"}
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "not_found"


def test_ask_question_rejects_another_seats_session() -> None:
    ws = _workspace()
    owner = _user(ws.id)
    other = _user(ws.id)
    sid = _seed_session(other)

    resp = _client(owner).post(
        "/api/v1/agent/messages", json={"session_id": str(sid), "question": "hello"}
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "forbidden"


def test_ask_question_guardrail_refuses_weight_claims_without_erroring() -> None:
    """A refusal is a fallback answer, not an error state (AGENT_DESIGN.md).

    This used to return ``422 guardrail_block``. The frontend already had to
    special-case that status into a `blockedAnswer` and still framed it as an
    error, so the contract now carries the refusal itself.
    """
    ws = _workspace()
    user = _user(ws.id)
    sid = _seed_session(user)

    answer = _ask(_client(user), sid, "how many kg of waste this week?")

    assert answer["refused"] is True
    assert answer["headline"] == "I can show surplus exposure, not measured waste."
    body = " ".join(answer["body"])
    assert "does not weigh anything" in body
    # Declining to give a weight must not smuggle one in: no 'kg' anywhere in
    # the prose the operator reads.
    assert "kg" not in body.lower()
    # Nothing was read, so the refusal cites no sources and claims no tools.
    assert answer["sources"] == []
    assert answer["tools_used"] == []


def test_a_preflight_refusal_costs_no_allowance() -> None:
    """A guardrail decline is not billed: nothing was planned or composed.

    AGENT_SERVICE.md counts a refusal reached *after* the model ran; this one is
    rejected before any work, so charging the operator for it would be billing
    them for our own decline.
    """
    ws = _workspace()
    user = _user(ws.id)
    sid = _seed_session(user)
    today = datetime.datetime.now(datetime.UTC).date().isoformat()

    _ask(_client(user), sid, "how many kg of waste this week?")

    assert agent_router._USAGE.get((user.id, today), 0) == 0
    # An answered question, for contrast, does consume one.
    _ask(_client(user), sid, "what should I prepare tomorrow?")
    assert agent_router._USAGE.get((user.id, today), 0) == 1


def test_ask_question_enforces_the_turn_limit() -> None:
    """The turn cap is the ``MAX_TURNS_PER_SESSION`` the client is handed."""
    ws = _workspace()
    user = _user(ws.id)
    sid = _seed_session(user)
    agent_router._SESSIONS[sid]["turn_count"] = agent_router.MAX_TURNS_PER_SESSION

    resp = _client(user).post(
        "/api/v1/agent/messages", json={"session_id": str(sid), "question": "hello"}
    )
    assert resp.status_code == 429, resp.text
    assert resp.json()["error"]["code"] == "turn_limit"


def test_ask_question_has_no_grounding_data_and_says_so() -> None:
    ws = _workspace()
    user = _user(ws.id)
    sid = _seed_session(user)

    answer = _ask(_client(user), sid, "what should I prepare tomorrow?")
    assert answer["headline"].startswith("No forecasts found for this workspace")
    assert {"label": "Status", "value": "No forecasts found"} in answer["figures"]
    assert answer["refused"] is False
    assert answer["sources"] == []


def test_ask_question_distinguishes_a_failed_quality_lookup_from_no_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A query error is not an empty result.

    This is the case the module used to answer with "No forecasts found for this
    workspace — generate a forecast first", which is a claim about the data.
    """
    ws = _workspace()
    user = _user(ws.id)
    sid = _seed_session(user)
    db = MagicMock()
    db.scalar.side_effect = RuntimeError("quality table unavailable")

    client = _client(user, db)
    with caplog.at_level(logging.WARNING, logger=agent_router.__name__):
        answer = _ask(client, sid, "why is the pipeline quality gap so large?")

    assert answer["headline"].startswith("Quality data could not be read")
    assert "No forecasts found" not in answer["headline"]
    assert {"label": "Status", "value": "Quality lookup failed"} in answer["figures"]
    assert "quality grounding query failed" in caplog.text


def test_ask_question_quality_intent_with_no_open_issues_says_no_data() -> None:
    """The query succeeded and found nothing — the honest answer is "no data"."""
    ws = _workspace()
    user = _user(ws.id)
    sid = _seed_session(user)
    db = MagicMock()
    db.scalar.return_value = None

    answer = _ask(_client(user, db), sid, "what is our pipeline quality gap?")
    assert answer["headline"].startswith("No forecasts found for this workspace")
    assert {"label": "Status", "value": "No forecasts found"} in answer["figures"]


def test_ask_question_grounds_a_quality_answer_in_the_open_issue() -> None:
    ws = _workspace()
    user = _user(ws.id)
    sid = _seed_session(user)
    db = MagicMock()
    # forecast lookup, policy lookup, then the quality issue
    db.scalar.side_effect = [None, None, _Issue("3 days of deliveries missing")]

    answer = _ask(_client(user, db), sid, "what is our quality gap?")
    assert answer["headline"] == "Quality check: 3 days of deliveries missing"
    assert "get_quality" in answer["tools_used"]
    assert {"label": "Severity", "value": "warning"} in answer["figures"]


def test_list_messages_rejects_another_seats_session() -> None:
    ws = _workspace()
    owner = _user(ws.id)
    other = _user(ws.id)
    sid = _seed_session(other)

    resp = _client(owner).get("/api/v1/agent/messages", params={"session_id": str(sid)})
    assert resp.status_code == 403, resp.text


def test_pin_answer_returns_404_for_an_unknown_turn() -> None:
    ws = _workspace()
    user = _user(ws.id)
    db = MagicMock()
    db.execute.return_value.first.return_value = None

    resp = _client(user, db).post(f"/api/v1/agent/messages/{uuid.uuid4()}/pin", json={})
    assert resp.status_code == 404, resp.text


# ------------------------------------------------ insights and quota reads ---


def test_get_insights_returns_an_empty_list_when_the_queries_fail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = _workspace()
    user = _user(ws.id)
    db = MagicMock()
    db.scalars.side_effect = RuntimeError("quality table unavailable")

    with caplog.at_level(logging.WARNING, logger=agent_router.__name__):
        resp = _client(user, db).get("/api/v1/agent/insights")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"data": []}
    # The empty list is indistinguishable from "no insights" unless the failure
    # is logged, which is the whole point of the warning.
    assert "could not read quality issues" in caplog.text


def test_get_insights_maps_an_open_issue_to_a_card() -> None:
    ws = _workspace()
    user = _user(ws.id)
    db = MagicMock()
    row = _Issue("3 days of deliveries missing", severity="critical")
    db.scalars.return_value.all.return_value = [row]

    resp = _client(user, db).get("/api/v1/agent/insights")
    assert resp.status_code == 200, resp.text
    cards = resp.json()["data"]
    assert len(cards) == 1
    assert cards[0]["tone"] == "risk"
    assert cards[0]["title"] == "3 days of deliveries missing"
    assert cards[0]["route"] == "quality"


def test_get_quota_reports_the_in_process_counter_when_there_is_no_row() -> None:
    ws = _workspace()
    user = _user(ws.id)
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    agent_router._USAGE[(user.id, today)] = 4
    db = MagicMock()
    db.execute.return_value.first.return_value = None  # nothing recorded for today yet

    resp = _client(user, db).get("/api/v1/agent/quota")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tier"] == "operator"
    assert body["used"] == 4
    assert body["limit"] == 60


def test_get_quota_prefers_the_stored_count_over_the_local_counter() -> None:
    ws = _workspace()
    user = _user(ws.id)
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    agent_router._USAGE[(user.id, today)] = 1
    db = MagicMock()
    db.execute.return_value.first.return_value = (9,)

    resp = _client(user, db).get("/api/v1/agent/quota")
    assert resp.json()["used"] == 9


def test_get_quota_ignores_a_non_numeric_stored_value() -> None:
    """A value that is not a count is not a usage figure."""
    ws = _workspace()
    user = _user(ws.id)
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    agent_router._USAGE[(user.id, today)] = 3
    db = MagicMock()
    db.execute.return_value.first.return_value = (MagicMock(),)

    resp = _client(user, db).get("/api/v1/agent/quota")
    assert resp.json()["used"] == 3


# ------------------------------------------------------------ admin surface --


# ---------------------------------------------------------- assistant voice --
#
# The panel renders `headline` then each `body` paragraph, and offers `action`
# as a button. An answer with no body, or a button into a screen the role cannot
# open, is a worse experience than the terse one these tests replaced.


def test_create_session_greets_the_caller() -> None:
    """Opening a session should welcome the user, not just mint an id."""
    ws = _workspace()
    user = _user(ws.id)
    resp = _client(user).post("/api/v1/agent/sessions", json={})
    assert resp.status_code == 201, resp.text

    greeting = resp.json()["greeting"]
    assert greeting.startswith("Hi Test")  # first name from users.name
    assert "DemandIQ assistant" in greeting
    # It states the evidence boundary up front, so the first thing a new user
    # learns is what the assistant will refuse to do.
    assert "rather than guess" in greeting


def test_answer_always_carries_a_body_so_the_panel_has_reasoning() -> None:
    """AGENT_DESIGN.md: headline, BODY, figures, sources — body not optional."""
    ws = _workspace()
    user = _user(ws.id)
    sid = _seed_session(user)

    answer = _ask(_client(user), sid, "what should I prepare tomorrow?")
    assert isinstance(answer["body"], list)
    assert answer["body"], "an answer whose only prose is the headline is not an answer"
    # The panel prints the headline and then the body, so a body that echoed the
    # headline would render the same line twice — the defect this field fixes.
    assert answer["headline"] not in answer["body"]


def test_no_data_answer_explains_what_is_missing() -> None:
    ws = _workspace()
    user = _user(ws.id)
    sid = _seed_session(user)

    answer = _ask(_client(user), sid, "what should I prepare tomorrow?")
    body = " ".join(answer["body"])
    assert "will not invent a number" in body
    assert "policy" in body.lower()
    assert {"label": "Next step", "value": "Generate a forecast"} in answer["figures"]
    assert answer["action"] == {"label": "Open the forecast", "route": "forecast"}


def test_no_user_facing_text_names_an_api_endpoint() -> None:
    """An operator asking a question is not a developer reading the OpenAPI spec."""
    ws = _workspace()
    user = _user(ws.id)
    sid = _seed_session(user)

    answer = _ask(_client(user), sid, "hello")
    blob = " ".join(
        [answer["headline"], *answer["body"], *(str(f["value"]) for f in answer["figures"])]
    )
    assert "POST /api/v1" not in blob
    assert "GET /api/v1" not in blob


@pytest.mark.parametrize("role", [UserRole.operator, UserRole.viewer])
def test_action_never_offers_a_screen_the_role_cannot_open(role: UserRole) -> None:
    """``navItems.ts`` gates these screens, so a hand-off must respect the role.

    Both roles here are barred from the administrator screens, so a button into
    one would land the user on a 403.
    """
    ws = _workspace()
    user = _user(ws.id, role=role)
    sid = _seed_session(user)

    answer = _ask(_client(user), sid, "what should I prepare tomorrow?")
    action = answer["action"]
    assert action is None or action["route"] in {"forecast", "analytics", "quality"}


def test_action_label_matches_the_screen_it_opens() -> None:
    """A button labelled for one screen must not open another.

    The operator role cannot reach the Policy Lab, so the hand-off falls back to
    the forecast screen — and the label has to follow the route, not the
    preference that was denied.
    """
    ws = _workspace()
    user = _user(ws.id)  # operator
    sid = _seed_session(user)
    db = MagicMock()
    db.scalar.side_effect = [_Forecast(point=164.0, low=141.0, high=189.0), None]

    action = _ask(_client(user, db), sid, "how many covers tomorrow?")["action"]
    assert action == {"label": "Open the forecast", "route": "forecast"}


def test_administrator_is_offered_the_policy_lab() -> None:
    """The same answer, for a role that can reach the more specific screen."""
    ws = _workspace()
    user = _user(ws.id, role=UserRole.administrator)
    sid = _seed_session(user)
    db = MagicMock()
    db.scalar.side_effect = [_Forecast(point=164.0, low=141.0, high=189.0), None]

    action = _ask(_client(user, db), sid, "how many covers tomorrow?")["action"]
    assert action == {"label": "Compare policies", "route": "admin-policies"}


def test_forecast_answer_interprets_the_interval() -> None:
    """The numbers must be explained, not merely restated as chips."""
    ws = _workspace()
    user = _user(ws.id)
    sid = _seed_session(user)
    db = MagicMock()
    # forecast lookup, then no persisted policy
    db.scalar.side_effect = [_Forecast(point=164.0, low=141.0, high=189.0), None]

    answer = _ask(_client(user, db), sid, "how many covers tomorrow?")
    assert "164" in answer["headline"]

    body = " ".join(answer["body"])
    assert "141" in body and "189" in body
    # A range with no interpretation is still just a number, so the body has to
    # say what the interval is for.
    assert "nine days in ten" in body
    # With no policy there is no preparation quantity, and the body has to say
    # so rather than imply one.
    assert "not how much to prepare" in body


def test_latency_is_measured_not_a_constant() -> None:
    """A hardcoded latency is a fabricated figure in an answer about real data."""
    ws = _workspace()
    user = _user(ws.id)
    sid = _seed_session(user)

    answer = _ask(_client(user), sid, "hello")
    assert isinstance(answer["latency_ms"], int)
    assert answer["latency_ms"] >= 1


def test_admin_agent_endpoints_require_the_administrator_role() -> None:
    ws = _workspace()
    operator = _user(ws.id, role=UserRole.operator)

    resp = _client(operator).get("/api/v1/admin/agent/quotas")
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "forbidden"


def test_admin_agent_quotas_lists_the_workspace_seats() -> None:
    ws = _workspace()
    admin = _user(ws.id, role=UserRole.administrator)
    db = MagicMock()
    # The seat-quota table is empty, so the endpoint falls back to the roster.
    db.execute.return_value.all.return_value = []
    db.scalars.return_value.all.return_value = [admin]

    resp = _client(admin, db).get("/api/v1/admin/agent/quotas")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert any(row.get("user_id") == str(admin.id) for row in data)


def test_admin_agent_quotas_name_each_seat_and_report_its_usage() -> None:
    """A seat row carries the person, their override and both usage counters.

    The endpoint used to select only ``user_id/tier_id/status`` from the quota
    table, so every seat rendered as a bare id with empty Today and This month
    bars — and opening a seat pre-filled its override blank, which meant saving
    an unrelated tier change wrote ``null`` over it.
    """
    ws = _workspace()
    admin = _user(ws.id, role=UserRole.administrator)
    db = MagicMock()

    def _execute(sql: object, params: object = None) -> MagicMock:
        if "agent_seat_quotas" in str(sql):
            return MagicMock(all=MagicMock(return_value=[(str(admin.id), "analyst", "active", 90)]))
        return MagicMock(all=MagicMock(return_value=[]))

    db.execute.side_effect = _execute
    db.scalars.return_value.all.return_value = [admin]

    today = datetime.datetime.now(datetime.UTC).date()
    # A second day inside the same calendar month, so month-to-date sums two
    # buckets while "today" counts one.
    other_day = today.replace(day=2) if today.day != 2 else today.replace(day=1)
    agent_router._USAGE[(admin.id, today.isoformat())] = 7
    agent_router._USAGE[(admin.id, other_day.isoformat())] = 100

    body = _client(admin, db).get("/api/v1/admin/agent/quotas").json()
    row = next(r for r in body["data"] if r["user_id"] == str(admin.id))

    assert row["name"] == admin.name
    assert row["email"] == admin.email
    assert row["tier"] == "analyst"
    assert row["daily_override"] == 90
    assert row["status"] == "active"
    assert row["limit"] == agent_router._QUOTA_TIERS["analyst"]["daily"]
    assert row["month_limit"] == agent_router._QUOTA_TIERS["analyst"]["monthly"]
    assert row["used_today"] == 7
    assert row["used_month"] == 107


def test_admin_agent_endpoints_tiers_require_the_administrator_role() -> None:
    ws = _workspace()
    operator = _user(ws.id, role=UserRole.operator)

    resp = _client(operator).get("/api/v1/admin/agent/tiers")
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "forbidden"


def test_admin_agent_tiers_expose_exactly_the_enforced_allowances() -> None:
    """The screen's figures must be the enforced ones, not a copy that can drift.

    Both sides are read from the objects the request path uses, so a tier edited
    in ``_QUOTA_TIERS`` cannot leave the administration screen advertising a
    limit the API does not apply. This is the regression the endpoint exists for:
    the UI previously carried its own table, which had drifted to a different
    ``monthly``, a different ``concurrent``, and a tier key (``admin``) that the
    backend never returns.
    """
    ws = _workspace()
    admin = _user(ws.id, role=UserRole.administrator)

    resp = _client(admin).get("/api/v1/admin/agent/tiers")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert {tier["id"] for tier in body["data"]} == set(agent_router._QUOTA_TIERS)
    for tier in body["data"]:
        enforced = agent_router._QUOTA_TIERS[tier["id"]]
        assert tier["daily"] == enforced["daily"], tier["id"]
        assert tier["monthly"] == enforced["monthly"], tier["id"]
        assert tier["concurrent"] == enforced["concurrent"], tier["id"]
        assert tier["tools"] == list(enforced["tools"]), tier["id"]

    # The conversation limits are the constants the router enforces and reports.
    assert body["session"] == {
        "idle_timeout_min": agent_router.IDLE_TIMEOUT_MIN,
        "max_duration_h": agent_router.MAX_SESSION_H,
        "turns": agent_router.MAX_TURNS_PER_SESSION,
        "burst_per_min": agent_router.BURST_PER_MIN,
    }


def test_admin_agent_tiers_name_the_tier_a_role_without_one_receives() -> None:
    """A viewer has no tier of its own, so the payload must say which one it gets.

    Otherwise the roster shows the seat's allowance as unknown, or the screen
    invents one — the old table claimed a ``viewer`` tier of 20/day that the API
    has never had.
    """
    ws = _workspace()
    admin = _user(ws.id, role=UserRole.administrator)
    viewer = _user(ws.id, role=UserRole.viewer)

    body = _client(admin).get("/api/v1/admin/agent/tiers").json()
    fallback = body["fallback_tier"]

    assert fallback == agent_router._DEFAULT_TIER_ID
    assert fallback in {tier["id"] for tier in body["data"]}
    # The fallback named in the payload is the tier the enforcement path is using.
    assert agent_router._quota_for(viewer) is agent_router._QUOTA_TIERS[fallback]


def test_admin_agent_tiers_use_the_role_names_the_ui_already_shows() -> None:
    """Tier names come from ROLE_LABELS, so the screen cannot rename a role.

    ``operator`` is "Data Clerk" everywhere else in the product; the old local
    table agreed by hand, and a hand-copied name is one that can drift.
    """
    ws = _workspace()
    admin = _user(ws.id, role=UserRole.administrator)

    tiers = {
        tier["id"]: tier["name"]
        for tier in _client(admin).get("/api/v1/admin/agent/tiers").json()["data"]
    }
    assert tiers["operator"] == "Data Clerk"
    assert tiers["analyst"] == "Analyst"
    assert tiers["administrator"] == "Administrator"
    assert tiers["owner"] == "Owner"


def test_admin_agent_tiers_list_the_smallest_allowance_first() -> None:
    ws = _workspace()
    admin = _user(ws.id, role=UserRole.administrator)

    ids = [tier["id"] for tier in _client(admin).get("/api/v1/admin/agent/tiers").json()["data"]]
    # Dict insertion order put the owner ahead of the administrator; the screen
    # reads as a ladder, so it is ordered by allowance.
    assert ids == ["operator", "analyst", "administrator", "owner"]


def test_admin_agent_tier_daily_is_the_allowance_the_request_path_enforces() -> None:
    """Every ``daily`` the screen lists is the count the request path allows.

    The screen reads its numbers from ``GET /admin/agent/tiers``. This walks that
    payload and, for each tier, proves one request short of ``daily`` is served
    while the ``daily``-th is refused — so a displayed allowance cannot outrun
    what the API actually applies. Read from the payload, not from
    ``_QUOTA_TIERS``, so it fails if the endpoint ever stops reading enforcement.
    """
    ws = _workspace()
    admin = _user(ws.id, role=UserRole.administrator)
    tiers = _client(admin).get("/api/v1/admin/agent/tiers").json()["data"]
    today = datetime.datetime.now(datetime.UTC).date().isoformat()

    for tier in tiers:
        user = _user(ws.id, role=UserRole(tier["id"]))
        daily = tier["daily"]

        agent_router._USAGE[(user.id, today)] = daily - 1
        allowed = _client(user).post("/api/v1/agent/sessions", json={})
        assert allowed.status_code == 201, f"{tier['id']}: {allowed.text}"

        agent_router._USAGE[(user.id, today)] = daily
        refused = _client(user).post("/api/v1/agent/sessions", json={})
        assert refused.status_code == 429, f"{tier['id']}: {refused.text}"
        assert refused.json()["error"]["code"] == "quota_exhausted_daily", tier["id"]


def test_admin_agent_sessions_resolve_the_seat_name() -> None:
    """The sessions list names the person; it must not print the raw user id.

    The screen falls back to the id when no name is sent, so the API resolves it
    here rather than leaving an opaque UUID as the user's name.
    """
    ws = _workspace()
    admin = _user(ws.id, role=UserRole.administrator)
    member = _user(ws.id)
    _seed_session(member)

    def _execute(sql: object, params: object = None) -> MagicMock:
        if "FROM users" in str(sql):
            return MagicMock(all=MagicMock(return_value=[(str(member.id), "Test User")]))
        return MagicMock(all=MagicMock(return_value=[]))

    db = MagicMock()
    db.execute.side_effect = _execute
    body = _client(admin, db).get("/api/v1/admin/agent/sessions").json()

    assert body["data"], body
    assert body["data"][0]["user_name"] == "Test User"


def test_admin_agent_sessions_state_the_location_and_turn_count() -> None:
    """The sessions list reports what it knows and omits what it does not.

    No device is recorded against a session, and a session recovered from the
    database has no turn count — the screen printed "0 turns", "idle —" and a
    bare "— ·" for both, which reads as measured values. The location is
    resolved to a name rather than the id the row stores.
    """
    ws = _workspace()
    admin = _user(ws.id, role=UserRole.administrator)
    member = _user(ws.id)
    location_id = uuid.uuid4()
    sid = _seed_session(member, location_id=location_id)
    agent_router._SESSIONS[sid]["turn_count"] = 4

    def _execute(sql: object, params: object = None) -> MagicMock:
        text = str(sql)
        if "FROM users" in text:
            return MagicMock(all=MagicMock(return_value=[(str(member.id), "Test User")]))
        if "FROM locations" in text:
            return MagicMock(all=MagicMock(return_value=[(str(location_id), "Remera Grill")]))
        return MagicMock(all=MagicMock(return_value=[]))

    db = MagicMock()
    db.execute.side_effect = _execute
    body = _client(admin, db).get("/api/v1/admin/agent/sessions").json()

    row = body["data"][0]
    assert row["user_name"] == "Test User"
    assert row["location"] == "Remera Grill"
    assert row["turn_count"] == 4
    assert row["last_seen_at"], row
    assert "device" not in row
