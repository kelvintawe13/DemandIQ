"""Webhook delivery — fan-out and retry behaviour, asserted directly.

Alerts now reach subscribers through :func:`app.services.alerting.raise_alert`,
which fans out with :func:`app.services.delivery.webhooks.fanout_event` in
dry-run mode. That makes the delivery layer load-bearing for paging an operator,
so it gets its own regression tests: who receives what, that the body can be
verified from the stored payload, and how the retry ladder behaves on a 5xx, a
transport error and a paused endpoint.

Hermetic — ``httpx.MockTransport`` for the wire, an in-memory session for the
log, no network and no database.
"""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

import httpx
import pytest

from app.models.quality import Alert
from app.models.webhook import WebhookDelivery, WebhookEndpoint
from app.models.workspace import Workspace
from app.services.alerting import raise_alert
from app.services.delivery import webhooks
from app.services.delivery.webhooks import (
    BACKOFF_BASE_SECONDS,
    MAX_ATTEMPTS,
    deliver_webhook,
    fanout_event,
    sign_payload,
    verify_signature,
)

# --------------------------------------------------------------------------- #
# Doubles
# --------------------------------------------------------------------------- #


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """In-memory session that honours the endpoints query's status filter."""

    def __init__(self, endpoints: list[WebhookEndpoint] | None = None) -> None:
        self.endpoints = list(endpoints or [])
        self.alerts: list[Alert] = []
        self.deliveries: list[dict[str, Any]] = []
        self._next_id = 1

    def add(self, obj: Any) -> None:
        if isinstance(obj, WebhookDelivery):
            # Mirror the real autoincrement primary key so rows are identifiable.
            obj.id = self._next_id
            self._next_id += 1
            self.deliveries.append(
                {
                    "row": obj,
                    "endpoint_id": obj.endpoint_id,
                    "event": obj.event,
                    "payload": obj.payload,
                    "response_code": obj.response_code,
                    "attempt": obj.attempt,
                    "delivered_at": obj.delivered_at,
                }
            )
        elif isinstance(obj, Alert):
            self.alerts.append(obj)

    def flush(self) -> None:
        return None

    def scalars(self, stmt: Any) -> _Result:
        text = str(stmt)
        if "webhook_endpoints" in text:
            return _Result([e for e in self.endpoints if e.status in ("active", "failing")])
        return _Result([])


def _workspace() -> Workspace:
    return Workspace(
        id=uuid.uuid4(),
        name="Test Co",
        business_type="cafe",
        country="RW",
        timezone="Africa/Kigali",
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _endpoint(ws_id: uuid.UUID, events: list[str], *, status: str = "active") -> WebhookEndpoint:
    return WebhookEndpoint(
        id=uuid.uuid4(),
        workspace_id=ws_id,
        url="https://example.com/hook",
        events=events,
        secret="s3cret",
        status=status,
        created_at=datetime.datetime.now(datetime.UTC),
    )


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _sequence_client(*codes: int) -> tuple[httpx.Client, list[str]]:
    """A client that answers with ``codes`` in order, recording each request."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("X-DemandIQ-Event", ""))
        code = codes[min(len(seen) - 1, len(codes) - 1)]
        return httpx.Response(code, request=request)

    return _client(handler), seen


# --------------------------------------------------------------------------- #
# Fan-out from an alert
# --------------------------------------------------------------------------- #


def test_alert_fanout_logs_one_delivery_per_subscribed_active_endpoint() -> None:
    ws = _workspace()
    subscribed = _endpoint(ws.id, ["alert.raised"])
    subscribed_to_both = _endpoint(ws.id, ["alert.raised", "alert.resolved"])
    wrong_event = _endpoint(ws.id, ["forecast.generated"])
    paused = _endpoint(ws.id, ["alert.raised"], status="paused")
    db = _FakeSession([subscribed, subscribed_to_both, wrong_event, paused])

    alert = raise_alert(
        db, workspace_id=ws.id, severity="critical", title="Demand drift drifting", body="PSI 0.31"
    )

    assert len(db.deliveries) == 2
    assert {d["endpoint_id"] for d in db.deliveries} == {subscribed.id, subscribed_to_both.id}
    for delivery in db.deliveries:
        assert delivery["event"] == "alert.raised"
        assert delivery["response_code"] == 200
        assert delivery["attempt"] == 1
        assert delivery["payload"]["data"]["alert_id"] == str(alert.id)
        assert delivery["delivered_at"] is not None


def test_stored_payload_verifies_under_the_endpoint_secret() -> None:
    """A receiver must be able to check the signature from what we store.

    The delivery log keeps the signed body dict, not the raw bytes, so the test
    pins the serialisation the sender used (compact separators, sorted keys) and
    proves the HMAC is keyed with *this* endpoint's secret — a receiver that
    cannot reconstruct the signature would have to trust the payload blindly.
    """
    ws = _workspace()
    ep = _endpoint(ws.id, ["alert.raised"])
    db = _FakeSession([ep])

    raise_alert(
        db, workspace_id=ws.id, severity="warning", title="Demand drift watch", body="PSI 0.12"
    )

    assert len(db.deliveries) == 1
    payload = db.deliveries[0]["payload"]
    assert {"event", "timestamp", "endpoint_id", "data"} <= set(payload)
    assert payload["endpoint_id"] == str(ep.id)

    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = sign_payload(ep.secret, raw)
    assert verify_signature(ep.secret, raw, signature) is True
    # A different endpoint's secret (or a tampered body) must not verify.
    assert verify_signature("other-secret", raw, signature) is False
    assert verify_signature(ep.secret, raw + b" ", signature) is False


def test_alerting_fanout_never_raises_when_a_delivery_explodes(monkeypatch) -> None:
    """Reporting a failure must not itself fail — the job still has to finish."""
    ws = _workspace()
    first = _endpoint(ws.id, ["alert.raised"])
    second = _endpoint(ws.id, ["alert.raised"])
    db = _FakeSession([first, second])

    calls: list[uuid.UUID] = []
    # Capture the real function first: fanout resolves ``deliver_webhook`` from
    # this module, so delegating to the patched name would recurse forever.
    real_deliver = webhooks.deliver_webhook

    def flaky(db_arg: Any, endpoint: Any, event: str, payload: dict, **kw: Any) -> Any:
        calls.append(endpoint.id)
        if endpoint.id == first.id:
            raise RuntimeError("tls handshake failed")
        return real_deliver(db_arg, endpoint, event, payload, **kw)

    monkeypatch.setattr(webhooks, "deliver_webhook", flaky)

    alert = raise_alert(db, workspace_id=ws.id, severity="info", title="Note", body="hello")

    assert alert.id is not None  # the alert is still persisted
    assert set(calls) == {first.id, second.id}  # the bad endpoint did not stop the good one
    assert len(db.deliveries) == 1
    assert db.deliveries[0]["endpoint_id"] == second.id


def test_fanout_without_endpoints_is_a_no_op() -> None:
    ws = _workspace()
    db = _FakeSession([])
    assert fanout_event(db, ws.id, "alert.raised", {"x": 1}) == []
    assert db.deliveries == []


# --------------------------------------------------------------------------- #
# Retry ladder
# --------------------------------------------------------------------------- #


def test_delivery_succeeds_after_transient_failures() -> None:
    ws = _workspace()
    ep = _endpoint(ws.id, ["alert.raised"])
    db = _FakeSession()
    client, seen = _sequence_client(503, 500, 200)

    result = deliver_webhook(db, ep, "alert.raised", {"msg": "retry me"}, http_client=client)

    assert seen == ["alert.raised"] * 3
    assert result.attempt == 3
    assert result.response_code == 200
    assert [d["attempt"] for d in db.deliveries] == [1, 2, 3]
    assert [d["response_code"] for d in db.deliveries] == [503, 500, 200]


def test_delivery_exhausts_every_attempt_on_a_persistent_5xx() -> None:
    ws = _workspace()
    ep = _endpoint(ws.id, ["alert.raised"])
    db = _FakeSession()
    client, seen = _sequence_client(500)

    result = deliver_webhook(db, ep, "alert.raised", {"msg": "always down"}, http_client=client)

    assert len(seen) == MAX_ATTEMPTS
    assert result.attempt == MAX_ATTEMPTS
    assert result.response_code == 500
    assert len(db.deliveries) == MAX_ATTEMPTS


def test_transport_error_is_recorded_as_response_code_zero() -> None:
    """A connection error is retried and logged, not raised into the caller."""
    ws = _workspace()
    ep = _endpoint(ws.id, ["alert.raised"])
    db = _FakeSession()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ConnectError("connection refused", request=request)

    result = deliver_webhook(
        db, ep, "alert.raised", {"msg": "unreachable"}, http_client=_client(handler)
    )

    assert len(attempts) == MAX_ATTEMPTS
    assert result.response_code == 0
    assert len(db.deliveries) == MAX_ATTEMPTS


def test_backoff_grows_exponentially_with_jitter() -> None:
    previous_ceiling = 0.0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        expected = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
        delay = webhooks._backoff_seconds(attempt)
        assert expected <= delay < expected + 0.5, f"attempt {attempt}: {delay}"
        assert delay > previous_ceiling / 2  # each rung is a step up, not a reset
        previous_ceiling = expected + 0.5


def test_paused_endpoint_refuses_delivery() -> None:
    ws = _workspace()
    ep = _endpoint(ws.id, ["alert.raised"], status="paused")
    db = _FakeSession()

    with pytest.raises(ValueError):
        deliver_webhook(db, ep, "alert.raised", {"msg": "x"}, dry_run=True)
    assert db.deliveries == []


def test_unknown_event_is_rejected_before_any_delivery() -> None:
    ws = _workspace()
    ep = _endpoint(ws.id, ["alert.raised"])
    db = _FakeSession()

    with pytest.raises(ValueError):
        deliver_webhook(db, ep, "not.a.real.event", {"msg": "x"}, dry_run=True)
    assert db.deliveries == []
    # The catalog is the gate: nothing is delivered for an event it does not list.
    assert fanout_event(db, ws.id, "not.a.real.event", {"msg": "x"}) == []


def test_dry_run_and_real_delivery_agree_on_the_body() -> None:
    """Dry-run is what alerting uses; it must log the same body a real POST signs."""
    ws = _workspace()
    ep = _endpoint(ws.id, ["alert.raised"])
    payload = {"msg": "same body", "n": 3}

    dry_db = _FakeSession()
    dry = deliver_webhook(dry_db, ep, "alert.raised", payload, dry_run=True)

    sent: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.content)
        return httpx.Response(200, request=request)

    real_db = _FakeSession()
    real = deliver_webhook(real_db, ep, "alert.raised", payload, http_client=_client(handler))

    assert dry.response_code == real.response_code == 200
    # ``timestamp`` legitimately differs between the two calls.
    assert {k: v for k, v in dry.payload.items() if k != "timestamp"} == {
        k: v for k, v in real.payload.items() if k != "timestamp"
    }
    assert sent == [json.dumps(real.payload, separators=(",", ":"), sort_keys=True).encode()]
