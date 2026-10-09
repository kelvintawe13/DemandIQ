"""Realtime tests: workspace fan-out, tenant isolation, and the socket.

The suite is hermetic in the same way ``test_shared_state.py`` is: Redis is
absent (``tests/conftest.py`` pins ``kv.get_redis`` to ``None``), so the hub runs
its in-process path, no bridge task is ever started and no thread is ever
spawned. That is the path CI exercises and the path a single-worker deployment
runs, and it is where the rules that matter — who hears what, and which direction
a topic may travel — have to hold. The Redis fan-out itself is a plumbing
detail on top of a registry that is already exercised.

The socket tests build a bare app around ``stream.router`` rather than importing
``app.main``: the two routes under test need no database, and importing the
application factory would drag in the whole router tree.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from collections.abc import AsyncIterator, MutableMapping
from types import SimpleNamespace
from typing import Any, cast

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

import app.services.kv as kv
import app.services.realtime as realtime
from app.models.device import DEVICE_STATUS_ACTIVE
from app.routers import stream
from app.security import device_auth
from app.security.jwt import create_access_token
from app.security.permissions import DEVICE_SYNC

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture(autouse=True)
async def _reset_default_hub() -> AsyncIterator[None]:
    """Leave no subscriber behind for the next test to trip over.

    The default hub is process-wide, and a subscription that outlives its test
    would make the isolation tests lie in the one direction that matters: a
    subscriber from an earlier test would hear a publish and look like a
    cross-tenant leak.
    """
    await realtime.reset_hub()
    yield
    await realtime.reset_hub()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(stream.router)
    return TestClient(app)


def _ws(headers: dict[str, str] | None = None, query: str = "") -> WebSocket:
    """A WebSocket with a hand-written scope — no server, no handshake."""
    raw = [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()]
    scope: dict[str, Any] = {
        "type": "websocket",
        "path": "/api/v1/stream/ws",
        "root_path": "",
        "scheme": "ws",
        "query_string": query.encode(),
        "headers": raw,
        "client": ("10.0.0.7", 51000),
        "path_params": {},
    }

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.connect"}

    async def send(_message: MutableMapping[str, Any]) -> None:
        return None

    return WebSocket(scope, receive, send)


def _event(workspace_id: uuid.UUID, event_id: str = "e1") -> realtime.RealtimeEvent:
    return realtime.RealtimeEvent(
        event_id=event_id,
        workspace_id=workspace_id,
        topic=realtime.TOPIC_DEMAND,
        payload={},
        published_at=datetime.datetime.now(datetime.UTC),
    )


def _device(
    subject: str | None = None, *, may_publish_demand: bool = True
) -> stream.SocketIdentity:
    """An identity standing in for an enrolled device on the given socket."""
    return stream.SocketIdentity(
        workspace_id=uuid.uuid4(),
        subject=subject or str(uuid.uuid4()),
        kind="device",
        may_publish_demand=may_publish_demand,
    )


# --------------------------------------------------------------------------- #
# Fan-out
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_publish_reaches_a_subscriber_in_the_same_workspace() -> None:
    """The whole point: an accepted reading reaches the devices watching."""
    workspace = uuid.uuid4()
    sub = realtime.subscribe(workspace, realtime.TOPIC_DEMAND)
    try:
        published = await realtime.publish(
            workspace, realtime.TOPIC_DEMAND, {"location_id": str(uuid.uuid4()), "demand_qty": 41}
        )
        received = await sub.get(timeout=0.5)
        assert received is not None
        assert received.event_id == published.event_id
        assert received.workspace_id == workspace
        assert received.topic == realtime.TOPIC_DEMAND
        assert received.payload["demand_qty"] == 41
    finally:
        sub.close()


@pytest.mark.asyncio
async def test_publish_does_not_reach_another_workspace() -> None:
    """Tenant isolation. Two workspaces, one topic, no crossing.

    The subscriber listens on a *different* workspace than the publisher, which
    is the shape of the bug this guards: same topic name, different tenant. If
    the registry were keyed on topic alone this is the test that fails.
    """
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    listener = realtime.subscribe(tenant_b, realtime.TOPIC_DEMAND)
    try:
        await realtime.publish(tenant_a, realtime.TOPIC_DEMAND, {"demand_qty": 900})
        # A timeout, not a sentinel value: an implementation that leaked would
        # deliver and this would return the event, not None.
        assert await listener.get(timeout=0.2) is None
        # ...and the same listener still hears its own workspace, so the
        # assertion above is about isolation and not about a dead hub.
        await realtime.publish(tenant_b, realtime.TOPIC_DEMAND, {"demand_qty": 3})
        assert await listener.get(timeout=0.5) is not None
    finally:
        listener.close()


@pytest.mark.asyncio
async def test_a_topic_only_reaches_its_own_subscribers() -> None:
    """Within one workspace, topics stay apart too."""
    workspace = uuid.uuid4()
    demand = realtime.subscribe(workspace, realtime.TOPIC_DEMAND)
    forecast = realtime.subscribe(workspace, realtime.TOPIC_FORECAST)
    try:
        await realtime.publish(workspace, realtime.TOPIC_FORECAST, {"p50": 12.5})
        assert await forecast.get(timeout=0.5) is not None
        assert await demand.get(timeout=0.2) is None
    finally:
        demand.close()
        forecast.close()


@pytest.mark.asyncio
async def test_every_subscriber_on_a_topic_receives() -> None:
    """One publish, N deliveries — a fan-out, not a queue handoff."""
    workspace = uuid.uuid4()
    subs = [realtime.subscribe(workspace, realtime.TOPIC_DEMAND) for _ in range(4)]
    try:
        published = await realtime.publish(workspace, realtime.TOPIC_DEMAND, {"demand_qty": 7})
        for sub in subs:
            received = await sub.get(timeout=0.5)
            assert received is not None, "a subscriber was skipped"
            assert received.event_id == published.event_id
    finally:
        for sub in subs:
            sub.close()


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_close_releases_the_registry_entry() -> None:
    """A subscription that outlives its socket is a leak the health route shows."""
    hub = realtime.get_hub()
    workspace = uuid.uuid4()
    sub = hub.subscribe(workspace, realtime.TOPIC_DEMAND)
    assert hub.subscriber_count() == 1
    assert hub.subscriber_count(workspace, realtime.TOPIC_DEMAND) == 1

    sub.close()
    assert hub.subscriber_count() == 0
    # The key itself goes, not just its last member: a process that has served a
    # thousand workspaces must not hold a thousand empty sets.
    assert hub.active_topics() == []

    sub.close()  # idempotent
    assert hub.subscriber_count() == 0


@pytest.mark.asyncio
async def test_closing_one_subscriber_keeps_the_others() -> None:
    """Detaching one client must not deafen the rest of the workspace."""
    hub = realtime.get_hub()
    workspace = uuid.uuid4()
    first = hub.subscribe(workspace, realtime.TOPIC_DEMAND)
    second = hub.subscribe(workspace, realtime.TOPIC_DEMAND)
    try:
        first.close()
        assert hub.subscriber_count(workspace, realtime.TOPIC_DEMAND) == 1
        await hub.publish(workspace, realtime.TOPIC_DEMAND, {"demand_qty": 1})
        assert await second.get(timeout=0.5) is not None
    finally:
        second.close()


@pytest.mark.asyncio
async def test_a_closed_subscription_releases_a_parked_consumer() -> None:
    """A reader blocked in ``get()`` must wake when the socket goes away."""
    workspace = uuid.uuid4()
    sub = realtime.subscribe(workspace, realtime.TOPIC_DEMAND)
    waiter = asyncio.ensure_future(sub.get())
    await asyncio.sleep(0)
    sub.close()
    assert await asyncio.wait_for(waiter, timeout=0.5) is None


@pytest.mark.asyncio
async def test_events_iterator_stops_when_the_subscription_closes() -> None:
    """The generator form a transport would use, ended by close()."""
    workspace = uuid.uuid4()
    sub = realtime.subscribe(workspace, realtime.TOPIC_DEMAND)
    seen: list[realtime.RealtimeEvent] = []

    async def consume() -> None:
        async for event in sub.events(heartbeat=0.05):
            seen.append(event)

    task = asyncio.ensure_future(consume())
    await realtime.publish(workspace, realtime.TOPIC_DEMAND, {"demand_qty": 5})
    await asyncio.sleep(0.05)
    sub.close()
    await asyncio.wait_for(task, timeout=0.5)
    assert len(seen) == 1


# --------------------------------------------------------------------------- #
# Direction
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_device_may_not_publish_a_forecast() -> None:
    """Forecasts are server→device only. A till cannot invent one.

    A forecast is a claim about an uncertain future carrying the model bundle and
    an ``as_of`` stamp. One minted on a device has provenance nobody can stand
    behind, so the hub refuses it at the boundary rather than filtering it at
    the socket — a caller that is not this transport gets the same answer.
    """
    workspace = uuid.uuid4()
    listener = realtime.subscribe(workspace, realtime.TOPIC_FORECAST)
    try:
        with pytest.raises(realtime.DirectionError) as exc:
            await realtime.publish(
                workspace,
                realtime.TOPIC_FORECAST,
                {"p50": 9999},
                origin=realtime.PublishOrigin.DEVICE,
            )
        assert exc.value.topic == realtime.TOPIC_FORECAST
        assert await listener.get(timeout=0.2) is None
    finally:
        listener.close()


@pytest.mark.asyncio
async def test_a_device_may_report_demand() -> None:
    """The other half of the asymmetry: demand flows both ways."""
    workspace = uuid.uuid4()
    sub = realtime.subscribe(workspace, realtime.TOPIC_DEMAND)
    try:
        await realtime.publish(
            workspace,
            realtime.TOPIC_DEMAND,
            {"location_id": str(uuid.uuid4()), "date": "2026-03-04", "demand_qty": 12},
            origin=realtime.PublishOrigin.DEVICE,
        )
        received = await sub.get(timeout=0.5)
        assert received is not None
        assert received.payload["demand_qty"] == 12
    finally:
        sub.close()


@pytest.mark.asyncio
async def test_the_server_may_publish_a_forecast() -> None:
    """The rule is on the *origin*, not on the topic: the server owns forecasts."""
    workspace = uuid.uuid4()
    sub = realtime.subscribe(workspace, realtime.TOPIC_FORECAST)
    try:
        await realtime.publish(workspace, realtime.TOPIC_FORECAST, {"p50": 120})
        assert await sub.get(timeout=0.5) is not None
    finally:
        sub.close()


@pytest.mark.asyncio
async def test_a_socket_frame_cannot_publish_a_forecast() -> None:
    """The transport refuses it too, with a reason the client can act on."""
    socket = stream._Socket(_device(), realtime.get_hub())
    try:
        reply = await stream._handle_frame(socket, {"type": "forecast", "payload": {"p50": 1}})
        assert reply is not None
        assert reply["type"] == "error"
        assert reply["error"]["code"] == "direction_not_allowed"
        assert realtime.get_hub().subscriber_count() == 0
    finally:
        await socket.aclose()


@pytest.mark.asyncio
async def test_a_socket_frame_may_report_demand() -> None:
    """A device frame fans out to the workspace, silently (no reply expected)."""
    identity = _device()
    hub = realtime.TopicHub()
    listener = hub.subscribe(identity.workspace_id, realtime.TOPIC_DEMAND)
    socket = stream._Socket(identity, hub)
    try:
        reply = await stream._handle_frame(
            socket, {"type": "demand", "payload": {"location_id": "x", "demand_qty": 8}}
        )
        assert reply is None
        received = await listener.get(timeout=0.5)
        assert received is not None
        assert received.payload["demand_qty"] == 8
    finally:
        await socket.aclose()
        listener.close()


@pytest.mark.asyncio
async def test_a_client_without_ingest_right_cannot_report_demand() -> None:
    """Reading forecasts and reporting demand are different capabilities."""
    socket = stream._Socket(_device(may_publish_demand=False), realtime.get_hub())
    try:
        reply = await stream._handle_frame(socket, {"type": "demand", "payload": {}})
        assert reply is not None
        assert reply["error"]["code"] == "forbidden"
    finally:
        await socket.aclose()


# --------------------------------------------------------------------------- #
# Bounded buffering
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_buffer_cap_is_enforced_by_dropping_the_oldest() -> None:
    """A subscriber that stops reading must not grow the process.

    Oldest-first is the right side to drop because the domain rule is already
    last-writer-wins: a device catching up after a stall wants the current value,
    not a backlog of values it has been superseded by.
    """
    workspace = uuid.uuid4()
    sub = realtime.subscribe(workspace, realtime.TOPIC_DEMAND, limit=3)
    try:
        for i in range(10):
            await realtime.publish(workspace, realtime.TOPIC_DEMAND, {"demand_qty": i})
        assert sub.dropped == 7
        held = [await sub.get(timeout=0.5) for _ in range(3)]
        assert [event.payload["demand_qty"] for event in held if event is not None] == [7, 8, 9]
        assert await sub.get(timeout=0.1) is None
    finally:
        sub.close()


@pytest.mark.asyncio
async def test_a_full_buffer_does_not_disturb_other_subscribers() -> None:
    """The cap is per subscriber: one stalled client is not a fleet-wide outage."""
    workspace = uuid.uuid4()
    stalled = realtime.subscribe(workspace, realtime.TOPIC_DEMAND, limit=1)
    healthy = realtime.subscribe(workspace, realtime.TOPIC_DEMAND, limit=64)
    try:
        for i in range(5):
            await realtime.publish(workspace, realtime.TOPIC_DEMAND, {"demand_qty": i})
        assert stalled.dropped == 4
        received = [await healthy.get(timeout=0.5) for _ in range(5)]
        assert all(event is not None for event in received)
    finally:
        stalled.close()
        healthy.close()


def test_the_default_buffer_limit_is_finite_and_documented() -> None:
    """A cap that is a constant can still be unbounded by accident; assert it."""
    assert 0 < realtime.DEFAULT_BUFFER_LIMIT <= 10_000
    assert "DEFAULT_BUFFER_LIMIT" in (realtime.__doc__ or "")


# --------------------------------------------------------------------------- #
# Health route
# --------------------------------------------------------------------------- #


def test_the_health_route_reports_degraded_without_redis() -> None:
    """An operator must be able to SEE the downgrade, not infer it from silence."""
    with _client() as client:
        response = client.get("/api/v1/stream/health")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "degraded"
    assert body["status"] == "degraded"
    assert body["delivery"] == "at-most-once"
    assert body["replay"] is False
    assert body["durable_source"] == "device_events"
    assert realtime.TOPIC_FORECAST in body["server_only_topics"]


def test_the_health_route_reports_redis_mode_when_the_broker_is_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same route must be able to say "healthy", or it is a false alarm."""
    monkeypatch.setattr(kv, "get_redis", lambda: object())
    with _client() as client:
        body = client.get("/api/v1/stream/health").json()
    assert body["mode"] == "redis"
    assert body["status"] == "ok"


def test_the_health_route_never_names_a_tenant() -> None:
    """It is unauthenticated, so it may not carry anything tenant-shaped."""
    with _client() as client:
        body = client.get("/api/v1/stream/health").json()
    assert set(body) == {
        "mode",
        "reason",
        "delivery",
        "replay",
        "durable_source",
        "buffer_limit",
        "topics",
        "server_only_topics",
        "subscribers",
        "active",
        "timestamp",
        "status",
    }
    assert body["subscribers"] == 0
    assert body["active"] == []


# --------------------------------------------------------------------------- #
# Socket authentication
# --------------------------------------------------------------------------- #


def test_a_socket_without_a_credential_is_refused() -> None:
    """No credential, no socket. The handshake is closed, not upgraded."""
    with _client() as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/v1/stream/ws"):
                pass
    assert exc.value.code == stream.WS_UNAUTHENTICATED


def test_a_socket_with_a_malformed_token_is_refused() -> None:
    """A bad signature is a 4401, and the refusal needs no database."""
    with _client() as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/v1/stream/ws?token=not-a-jwt"):
                pass
    assert exc.value.code == stream.WS_UNAUTHENTICATED


def test_a_socket_for_an_unknown_user_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A correctly signed token for a principal that is gone is still refused.

    The session factory is stubbed so this exercises the auth path end to end
    with no database: the token is real, and the user behind it is not.
    """

    class _EmptySession:
        def get(self, model: Any, key: Any) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(stream, "get_session_factory", lambda: _EmptySession)
    token = create_access_token(
        subject=str(uuid.uuid4()), workspace_id=str(uuid.uuid4()), role="owner"
    )
    with _client() as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/api/v1/stream/ws?token={token}"):
                pass
    assert exc.value.code == stream.WS_UNAUTHENTICATED


def test_the_token_may_arrive_as_a_subprotocol() -> None:
    """A browser cannot set headers, so the subprotocol is the browser's path.

    This one is checked at the extractor rather than through a live socket: the
    subprotocol only has to be echoed back if the handshake gets as far as
    ``accept``, and a 4401 happens before that.
    """
    credential = stream._extract_credential(
        _ws({"sec-websocket-protocol": "bearer, jwthunter-token"})
    )
    assert credential is not None
    assert credential.kind == "user"
    assert credential.secret == "jwthunter-token"
    assert credential.subprotocol == stream._SUBPROTOCOL


def test_the_authorization_header_is_preferred_over_the_query_string() -> None:
    """Header first: it is the transport that touches the fewest logs."""
    credential = stream._extract_credential(
        _ws({"authorization": "Bearer from-header"}, query="token=from-query")
    )
    assert credential is not None
    assert credential.kind == "user"
    assert credential.secret == "from-header"
    assert credential.subprotocol == ""


def test_the_device_credential_is_preferred_over_a_user_token() -> None:
    """A till never has a person's JWT; a client with both has a bug.

    The device header wins because it names what the client actually is, and
    ``device_auth`` deliberately keeps the two mechanisms in different header
    slots so neither can be mistaken for the other.
    """
    credential = stream._extract_credential(
        _ws({"x-device-key": "did_0123456789ab.abc", "authorization": "Bearer from-header"})
    )
    assert credential is not None
    assert credential.kind == "device"
    assert credential.secret == "did_0123456789ab.abc"


def test_the_query_string_is_the_last_resort_and_is_warned_about(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A token in a query string is a token in every access log; say so out loud."""
    with caplog.at_level("WARNING"):
        credential = stream._extract_credential(_ws(query="token=from-query"))
    assert credential is not None
    assert credential.secret == "from-query"
    assert credential.subprotocol == ""
    assert any("query string" in record.message for record in caplog.records)


def test_no_credential_anywhere_is_reported_as_none() -> None:
    """The unauthenticated path: nothing found, and nothing to echo."""
    assert stream._extract_credential(_ws()) is None
    assert stream._extract_credential(_ws({"authorization": "Bearer  "})) is None
    assert stream._extract_credential(_ws(query="token=")) is None


def test_a_socket_with_an_unknown_device_credential_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The device header is verified, not merely read.

    A well-formed but unknown credential goes through ``authenticate``'s decoy
    path and comes back 401 — which is what proves this route actually checks the
    device key instead of waving anything in that header through.
    """

    class _EmptyFleet:
        def scalars(self, *_args: Any, **_kwargs: Any) -> Any:
            return SimpleNamespace(all=lambda: [])

        def close(self) -> None:
            return None

    monkeypatch.setattr(stream, "get_session_factory", lambda: _EmptyFleet)
    presented = f"did_{'a' * 12}.{'b' * 43}"
    with _client() as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                "/api/v1/stream/ws", headers={device_auth.CREDENTIAL_HEADER: presented}
            ):
                pass
    assert exc.value.code == stream.WS_UNAUTHENTICATED


def test_a_device_without_the_sync_scope_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enrollment seeds ``device:sync``; a device without it does not stream.

    Called directly rather than over a socket: the app refuses in microseconds
    here, which races Starlette's test client's own session setup.
    """
    row = SimpleNamespace(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        label="back-office tablet",
        location_id=None,
        scopes=[],
        status=DEVICE_STATUS_ACTIVE,
    )
    monkeypatch.setattr(device_auth, "authenticate", lambda _db, _key: row)
    with pytest.raises(stream._AuthError) as exc:
        stream._identity_from_device(cast(Any, object()), "did_0123456789ab.abc")
    assert exc.value.code == stream.WS_FORBIDDEN
    assert "device:sync" in exc.value.reason


def test_a_revoked_device_is_refused_with_403_not_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A good secret on a revoked device is 403, so the fleet can diagnose it.

    ``device_auth`` draws this line only after the hash matches; the socket has
    to carry it through unchanged, or a revoked till reports "bad credential"
    and its operator goes looking for a rotation problem that does not exist.
    """

    def _refuse(_db: Any, _key: str) -> Any:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "device_revoked", "message": "This device is revoked"}},
        )

    monkeypatch.setattr(device_auth, "authenticate", _refuse)
    with pytest.raises(stream._AuthError) as exc:
        stream._identity_from_device(cast(Any, object()), "did_0123456789ab.abc")
    assert exc.value.code == stream.WS_FORBIDDEN
    assert exc.value.reason == "This device is revoked"


def test_a_scoped_device_receives_a_workspace_scoped_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy device path: bound to its own tenant, allowed to report demand.

    A stubbed ``authenticate`` and session, so this asserts the *wiring* — the
    workspace comes from the device row and nowhere else, and a socket never
    gets to choose it.
    """
    workspace = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        workspace_id=workspace,
        label="till 4",
        location_id=uuid.uuid4(),
        scopes=[DEVICE_SYNC],
        status=DEVICE_STATUS_ACTIVE,
    )
    committed: list[bool] = []
    fake_db = SimpleNamespace(
        scalars=lambda *_a, **_k: SimpleNamespace(all=lambda: [row]),
        flush=lambda: None,
        commit=lambda: committed.append(True),
        close=lambda: None,
    )
    monkeypatch.setattr(device_auth, "authenticate", lambda _db, _key: row)
    identity = stream._identity_from_device(cast(Any, fake_db), "did_0123456789ab.abc")

    assert identity.workspace_id == workspace
    assert identity.kind == "device"
    assert identity.may_publish_demand is True
    # The last-seen stamp ``authenticate`` flushed is committed, or the fleet
    # console would show a connected device as stale.
    assert committed == [True]


# --------------------------------------------------------------------------- #
# Frame handling
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_subscribe_and_unsubscribe_move_the_socket_registration() -> None:
    """A client picks its own topics, and detaching really detaches."""
    hub = realtime.TopicHub()
    identity = _device()
    socket = stream._Socket(identity, hub)
    try:
        socket.attach(realtime.TOPIC_ALERT)
        assert set(socket.subs) == {realtime.TOPIC_ALERT}
        assert hub.subscriber_count(identity.workspace_id, realtime.TOPIC_ALERT) == 1

        reply = await stream._handle_frame(
            socket, {"type": "unsubscribe", "topic": realtime.TOPIC_ALERT}
        )
        assert reply == {"type": "unsubscribed", "topic": realtime.TOPIC_ALERT}
        assert socket.subs == {}
        assert hub.subscriber_count() == 0
    finally:
        await socket.aclose()


@pytest.mark.asyncio
async def test_unknown_topics_and_malformed_frames_are_answered_not_raised() -> None:
    """A malformed frame is the client's problem, and it gets told."""
    socket = stream._Socket(_device(), realtime.get_hub())
    try:
        for frame, code in (
            ("not-an-object", "bad_frame"),
            ({}, "bad_frame"),
            ({"type": "subscribe", "topic": "everything"}, "unknown_topic"),
            ({"type": "wipe"}, "bad_frame"),
            ({"type": "demand"}, "bad_frame"),
        ):
            reply = await stream._handle_frame(socket, frame)
            assert reply is not None, frame
            assert reply["error"]["code"] == code, frame
    finally:
        await socket.aclose()


@pytest.mark.asyncio
async def test_ping_is_answered() -> None:
    """Keepalive from the client side, so an idle NAT does not eat the socket."""
    socket = stream._Socket(_device(), realtime.get_hub())
    try:
        reply = await stream._handle_frame(socket, {"type": "ping"})
        assert reply is not None
        assert reply["type"] == "pong"
    finally:
        await socket.aclose()


@pytest.mark.asyncio
async def test_closing_a_socket_releases_every_subscription() -> None:
    """The transport's teardown path, which is the leak that would ship."""
    hub = realtime.TopicHub()
    socket = stream._Socket(_device(), hub)
    for topic in (realtime.TOPIC_DEMAND, realtime.TOPIC_FORECAST, realtime.TOPIC_ALERT):
        socket.attach(topic)
    assert hub.subscriber_count() == 3

    await socket.aclose()
    assert hub.subscriber_count() == 0
    assert hub.active_topics() == []


# --------------------------------------------------------------------------- #
# The pump, end to end
#
# A stand-in for the socket, so the two directions of the transport loop are
# exercised without a handshake. ``TestClient`` covers the handshake (and only
# ever the refusal path, since accepting needs a database); this covers the
# bytes that follow it.
# --------------------------------------------------------------------------- #

_DISCONNECT = object()


class _Wire:
    """A WebSocket with the two methods the pump actually calls."""

    def __init__(self) -> None:
        self.sent: list[Any] = []
        self.inbox: asyncio.Queue[Any] = asyncio.Queue()

    async def send_json(self, frame: Any) -> None:
        self.sent.append(frame)

    async def receive_json(self) -> Any:
        frame = await self.inbox.get()
        if frame is _DISCONNECT:
            raise WebSocketDisconnect(code=1000)
        return frame

    async def settle(self, count: int = 1) -> None:
        """Let the pump's tasks run until ``count`` frames have gone out."""
        for _ in range(200):
            if len(self.sent) >= count:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"only {len(self.sent)} of {count} frames arrived")


@pytest.mark.asyncio
async def test_a_published_event_is_written_to_the_socket() -> None:
    """Server → client, the whole way: hub, forwarder, sink, writer."""
    hub = realtime.TopicHub()
    identity = _device()
    wire = _Wire()
    socket = stream._Socket(identity, hub)
    socket.attach(realtime.TOPIC_DEMAND)
    pump = asyncio.ensure_future(stream._pump(wire, socket))  # type: ignore[arg-type]
    try:
        await hub.publish(identity.workspace_id, realtime.TOPIC_DEMAND, {"demand_qty": 21})
        await wire.settle()
        frame = wire.sent[0]
        assert frame["topic"] == realtime.TOPIC_DEMAND
        assert frame["workspace_id"] == str(identity.workspace_id)
        assert frame["payload"]["demand_qty"] == 21

        # ...and a disconnect ends the loop rather than hanging it, surfacing as
        # the WebSocketDisconnect the endpoint catches.
        await wire.inbox.put(_DISCONNECT)
        with pytest.raises(WebSocketDisconnect):
            await asyncio.wait_for(pump, timeout=1.0)
    finally:
        await socket.aclose()
        pump.cancel()


@pytest.mark.asyncio
async def test_a_client_frame_travels_the_other_way_and_comes_back() -> None:
    """Client → server → client: a ping is answered on the same socket."""
    hub = realtime.TopicHub()
    wire = _Wire()
    socket = stream._Socket(_device(), hub)
    socket.attach(realtime.TOPIC_DEMAND)
    pump = asyncio.ensure_future(stream._pump(wire, socket))  # type: ignore[arg-type]
    try:
        await wire.inbox.put({"type": "ping"})
        await wire.settle()
        assert wire.sent[0]["type"] == "pong"

        # A forecast frame is refused on the wire, not just in the hub.
        await wire.inbox.put({"type": "forecast", "payload": {"p50": 1}})
        await wire.settle(2)
        assert wire.sent[1]["error"]["code"] == "direction_not_allowed"

        await wire.inbox.put(_DISCONNECT)
        with pytest.raises(WebSocketDisconnect):
            await asyncio.wait_for(pump, timeout=1.0)
    finally:
        await socket.aclose()
        pump.cancel()


# --------------------------------------------------------------------------- #
# Degradation plumbing
# --------------------------------------------------------------------------- #


def test_publishing_without_redis_is_not_an_error() -> None:
    """A notify must never fail the write that produced it."""
    assert kv.get_redis() is None
    assert kv.get_pubsub() is None
    assert kv.publish_message("diq:rt:abc:demand", "{}") is False
    assert kv.redis_enabled() is False
    assert kv.redis_degraded_reason() is None or isinstance(kv.redis_degraded_reason(), str)


def test_redis_publish_reports_whether_the_broker_took_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caller must be able to tell "nobody was told" from "it worked"."""
    published: list[tuple[str, str]] = []

    class _FakeRedis:
        def publish(self, channel: str, message: str) -> int:
            published.append((channel, message))
            return 1

    monkeypatch.setattr(kv, "get_redis", lambda: _FakeRedis())
    assert kv.publish_message("diq:rt:w:demand", "payload") is True
    assert published == [("diq:rt:w:demand", "payload")]


def test_redis_publish_survives_a_broker_that_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead broker is a logged downgrade, never an exception on a notify."""

    class _DeadRedis:
        def publish(self, channel: str, message: str) -> int:
            raise ConnectionError("broker gone")

    monkeypatch.setattr(kv, "get_redis", lambda: _DeadRedis())
    assert kv.publish_message("diq:rt:w:demand", "payload") is False


def test_channels_are_namespaced_and_workspace_scoped() -> None:
    """The broker separates tenants too, so a misconfigured consumer is contained."""
    workspace = uuid.uuid4()
    assert kv.channel("rt", str(workspace), "demand") == f"diq:rt:{workspace}:demand"
    assert realtime.CHANNEL_PREFIX == "diq:rt"


@pytest.mark.asyncio
async def test_a_message_this_worker_published_is_not_delivered_twice() -> None:
    """The bridge ignores its own envelope; local fan-out already happened.

    Without the self-filter, a worker that both publishes to Redis and reads its
    own pattern subscription would deliver every event to every local subscriber
    exactly twice.
    """
    hub = realtime.TopicHub()
    workspace = uuid.uuid4()
    sub = hub.subscribe(workspace, realtime.TOPIC_DEMAND)
    try:
        event = _event(workspace)
        envelope = kv.encode_value({"worker": realtime._INSTANCE_ID, "event": event.to_envelope()})
        hub._absorb({"type": "message", "data": envelope})
        assert await sub.get(timeout=0.2) is None

        foreign = kv.encode_value({"worker": "another-worker", "event": event.to_envelope()})
        hub._absorb({"type": "message", "data": foreign})
        received = await sub.get(timeout=0.5)
        assert received is not None
        assert received.event_id == "e1"
    finally:
        sub.close()


def test_a_foreign_or_malformed_envelope_is_ignored_not_fatal() -> None:
    """Anything else on the namespace is a message, not a crash."""
    hub = realtime.TopicHub()
    hub._absorb({"type": "subscribe", "data": 1})
    hub._absorb({"type": "message", "data": "not-json"})
    hub._absorb({"type": "message", "data": kv.encode_value({"worker": "w"})})
    hub._absorb({"type": "message", "data": kv.encode_value({"worker": "w", "event": {"nope": 1}})})
    assert hub.subscriber_count() == 0


def test_the_status_frame_states_the_delivery_guarantee() -> None:
    """The docstring's promise has to be visible to whoever is being paged."""
    status = realtime.realtime_status()
    assert status["delivery"] == "at-most-once"
    assert status["replay"] is False
    assert status["durable_source"] == "device_events"


def test_a_closed_subscription_refuses_new_events() -> None:
    """Off the registry means off the fan-out, not merely unseen."""
    workspace = uuid.uuid4()
    hub = realtime.TopicHub()
    sub = hub.subscribe(workspace, realtime.TOPIC_DEMAND)
    sub.close()
    assert sub.offer(_event(workspace, "e2")) is False


def test_a_subscription_needs_a_buffer_of_at_least_one() -> None:
    """A zero-sized buffer would make every publish a drop and look like a bug."""
    with pytest.raises(ValueError):
        realtime.TopicHub().subscribe(uuid.uuid4(), realtime.TOPIC_DEMAND, limit=0)


def test_subscriber_counting_needs_both_filters_or_neither() -> None:
    """Half a filter would be a silent full-registry scan."""
    with pytest.raises(ValueError):
        realtime.get_hub().subscriber_count(topic=realtime.TOPIC_DEMAND)
