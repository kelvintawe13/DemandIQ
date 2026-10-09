"""Realtime transport — the WebSocket a device or console holds open.

* GET  /api/v1/stream/health
* WS   /api/v1/stream/ws

The hub that decides *who* hears an event is :mod:`app.services.realtime`; this
module is only the pipe. Two things it must get right on its own:

**Authentication, explicitly.** FastAPI's dependency system does not give a
WebSocket the treatment an HTTP route gets: a dependency that raises
``HTTPException`` on a socket cannot produce a 401 response, because the
handshake has not completed and there is no response left to carry it. The
credential is therefore read and checked in the endpoint, and failure ends the
socket with a close code — 4401 unauthenticated, 4403 authenticated but not
allowed. An unauthenticated socket is never accepted.

Two credentials are accepted, because the two kinds of client are different
things, and they live in different header slots so neither can be substituted for
the other by accident:

* ``X-Device-Key: did_….…`` — an enrolled device, verified by
  :func:`app.security.device_auth.authenticate`. This is the credential a till
  actually holds, and it is checked first: a device never has a person's JWT, so
  a client presenting both is a client with a bug, and preferring the device
  credential is the reading that matches what the device is.
* ``Authorization: Bearer <jwt>`` — a human, resolved through the same checks
  ``get_current_user`` makes. Three transports are tried for it, in order:
  ``1.`` the header, which touches neither a log nor a proxy's header log;
  ``2.`` a WebSocket **subprotocol**, ``Sec-WebSocket-Protocol: bearer, <jwt>``,
  answered with ``bearer`` — this is how a browser does it, since the
  ``WebSocket`` constructor cannot set headers, and unlike a query parameter it
  is not written to access logs (though a proxy that logs headers still sees
  it); ``3.`` ``?token=<jwt>``, a last resort for clients that control neither.
  **Query strings are recorded in access logs, proxy logs and browser history,
  and a token in one of those is a token anybody reading them can replay.** It is
  logged as a warning when used, and it should go away once every client can send
  a header.

Whatever authenticates, the socket is **workspace-scoped**: the client is bound
to the workspace in its own credential and can only subscribe to topics of that
workspace, because the hub keys subscriptions by ``(workspace_id, topic)``. No
route here accepts a workspace id from the client, so there is no tenant check
in this module that could be forgotten.

The credential is checked once, at the handshake. A user session revoked while
the socket is open keeps receiving until it drops — the same ceiling every
long-lived connection has, bounded by the token's own expiry. Ending on
revocation would need a server→client control frame that this transport does not
define yet; the alternative — re-decoding the token on every push — is a
per-message cost bought for a guarantee nobody asked for. A *revoked device* is
different: its credential is checked at connect, so a device that is revoked
while connected is one whose socket outlives its revocation by the length of one
push, and closing revoked devices proactively is a fleet-wide sweep that belongs
to :mod:`app.routers.devices`.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.database import get_session_factory
from app.models.user import User
from app.security import device_auth
from app.security.deps import _require_live_session
from app.security.jwt import decode_token
from app.security.permissions import (
    DEVICE_SYNC,
    DEVICES_READ,
    INGEST_WRITE,
    has_any_capability,
    has_capability,
)
from app.services import realtime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/stream", tags=["stream"])

# Application close codes. 1000/1001 are protocol-level; the 4xxx range is the
# one RFC 6455 reserves for applications, so a client can tell "the server said
# no" from "the network went away" — the difference between re-authenticating
# and reconnecting.
WS_UNAUTHENTICATED = 4401
WS_FORBIDDEN = 4403

#: Idle interval at which the server pings, so an intermediary does not reap a
#: connection that a steady fleet leaves silent for hours.
HEARTBEAT_SECONDS = 25.0

#: Events waiting to go out on one socket. Bounded for the same reason a
#: subscription's own buffer is: a client that stops reading must not be able to
#: grow this process. Oldest is evicted first, and the count is reported in the
#: heartbeat so the loss is visible to whoever is watching.
SOCKET_QUEUE_LIMIT = realtime.DEFAULT_BUFFER_LIMIT

_SUBPROTOCOL = "bearer"

# Topics a socket is attached to on connect: the two a fleet actually
# synchronises on. ``device`` and ``alert`` are opt-in, because a till that
# subscribed to fleet-wide chatter would carry traffic it cannot act on.
_DEFAULT_TOPICS = (realtime.TOPIC_DEMAND, realtime.TOPIC_FORECAST)


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SocketIdentity:
    """Who is on the other end, resolved once at the handshake.

    ``kind`` is ``"user"`` or ``"device"``, and ``subject`` is the user id or the
    device id accordingly — the same field either way, because everything
    downstream of here is tenant-scoped and topic-scoped, not credential-scoped.
    ``may_publish_demand`` is the ingest decision, frozen here rather than
    re-derived per frame so nothing a socket says can widen it.
    """

    workspace_id: uuid.UUID
    subject: str
    kind: str
    may_publish_demand: bool

    def as_wire(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "kind": self.kind,
            "workspace_id": str(self.workspace_id),
        }


class _AuthError(Exception):
    """Authentication failed; carries the close code to answer with."""

    def __init__(self, code: int, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _Credential:
    """A credential found in the handshake, not yet resolved to a principal.

    Separating "was one presented" from "who is it" is what lets the 4401 path
    for a missing or badly-signed token run without a database at all.
    """

    kind: str
    secret: str
    subprotocol: str = ""


# --------------------------------------------------------------------------- #
# Credential extraction
# --------------------------------------------------------------------------- #


def _extract_credential(websocket: WebSocket) -> _Credential | None:
    """Find a credential, and say which transport it arrived on.

    The device header is read first, for the reason in the module docstring. The
    returned ``subprotocol`` is what the handshake must acknowledge (empty when
    none was used): a subprotocol the client offered and the server did not echo
    is a protocol error the client library reports as a failed connection.
    """
    device_key = websocket.headers.get(device_auth.CREDENTIAL_HEADER, "").strip()
    if device_key:
        return _Credential("device", device_key)

    header = websocket.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[len("bearer ") :].strip()
        if token:
            return _Credential("user", token)

    offered = [
        part.strip() for part in websocket.headers.get("sec-websocket-protocol", "").split(",")
    ]
    for index, part in enumerate(offered):
        if part == _SUBPROTOCOL and index + 1 < len(offered) and offered[index + 1]:
            return _Credential("user", offered[index + 1], _SUBPROTOCOL)

    token = websocket.query_params.get("token", "").strip()
    if token:
        logger.warning(
            "stream credential read from the query string (client=%s); query strings "
            "are written to access logs; send a header instead",
            websocket.client.host if websocket.client else "unknown",
        )
        return _Credential("user", token)
    return None


def _authenticate_token(token: str) -> dict[str, Any]:
    """Verify signature and claims, with no database access.

    Split from the user lookup so a malformed or expired token is refused before
    anything opens a connection — the 4401 path then needs no database at all,
    which is also what lets the transport be tested without one.
    """
    try:
        payload = decode_token(token)
    except Exception:
        raise _AuthError(WS_UNAUTHENTICATED, "Invalid or expired token") from None
    if payload.get("type") != "access":
        raise _AuthError(WS_UNAUTHENTICATED, "Invalid token type")
    if not payload.get("sub"):
        raise _AuthError(WS_UNAUTHENTICATED, "Invalid token payload")
    return payload


def _identity_from_user(db: Session, payload: dict[str, Any]) -> SocketIdentity:
    """Resolve verified token claims to a live user of a live workspace.

    The checks mirror ``get_current_user`` — a deactivated user and a revoked
    session are both refused — because a WebSocket bypasses that dependency
    entirely, and a socket that outlives a sign-out is worse than a request that
    does: a request fails on its next call, the socket does not fail at all.

    The token itself is already verified by :func:`_authenticate_token`, which
    runs first so a bad signature never opens a database session.
    """
    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except ValueError:
        raise _AuthError(WS_UNAUTHENTICATED, "Invalid user id") from None

    user = db.get(User, user_id)
    if user is None or user.deactivated_at is not None:
        raise _AuthError(WS_UNAUTHENTICATED, "User not found or deactivated")
    try:
        _require_live_session(db, user, payload)
    except HTTPException:
        raise _AuthError(WS_UNAUTHENTICATED, "This session is no longer signed in") from None

    # ``DEVICE_SYNC`` is a device-scope capability that no user role carries, so
    # the console side of the fleet is gated on ``DEVICES_READ``.
    if not has_any_capability(user, DEVICE_SYNC, DEVICES_READ):
        raise _AuthError(WS_FORBIDDEN, "This role may not open a fleet stream")

    return SocketIdentity(
        workspace_id=user.workspace_id,
        subject=str(user.id),
        kind="user",
        may_publish_demand=has_capability(user, INGEST_WRITE),
    )


def _identity_from_device(db: Session, presented: str) -> SocketIdentity:
    """Resolve an ``X-Device-Key`` credential to an active, sync-scoped device.

    ``authenticate`` already draws the line this module cares about: it refuses
    a revoked or quarantined device, and it does so *after* the secret verifies,
    so the close code distinguishes "you are who you say you are, and you are off"
    from "that is not a credential" without giving the second answer away to
    anyone who is not already holding the secret.
    """
    try:
        row = device_auth.authenticate(db, presented)
    except HTTPException as exc:
        code = WS_FORBIDDEN if exc.status_code == 403 else WS_UNAUTHENTICATED
        raise _AuthError(code, _reason_of(exc)) from None

    principal = device_auth.DevicePrincipal(
        device_id=row.id,
        workspace_id=row.workspace_id,
        label=row.label,
        location_id=row.location_id,
        scopes=list(row.scopes or []),
        status=row.status,
    )
    # Base scopes, not the union with live grants: ``device_auth`` resolves a
    # grant in the layer that owns it, and re-deriving that here would give two
    # answers to "may this device sync" depending on who asked.
    try:
        device_auth.require_device_scope(principal, DEVICE_SYNC)
    except HTTPException as exc:
        raise _AuthError(WS_FORBIDDEN, _reason_of(exc)) from None

    # ``authenticate`` stamped ``last_seen_at`` and flushed. Without this commit
    # the fleet console's staleness display would be a lie for a device that is
    # demonstrably connected — and a failed heartbeat must not cost the socket.
    try:
        db.commit()
    except Exception:
        logger.warning("could not record last-seen for device %s", row.id, exc_info=True)

    return SocketIdentity(
        workspace_id=row.workspace_id,
        subject=str(row.id),
        kind="device",
        # ``device:sync`` is the whole point of the credential: a device that
        # holds it reports demand and reads forecasts.
        may_publish_demand=True,
    )


def _reason_of(exc: HTTPException) -> str:
    """The message out of an ``HTTPException``'s envelope, or a generic one."""
    detail = exc.detail
    if isinstance(detail, dict):
        message = detail.get("error", {}).get("message")
        if isinstance(message, str):
            return message
    return "Authentication failed"


# --------------------------------------------------------------------------- #
# Socket state
# --------------------------------------------------------------------------- #


class _Socket:
    """Server-side state for one open socket: its subscriptions and its writer.

    Every subscription gets a forwarder that pushes into one bounded sink queue,
    and exactly one task writes to the socket. That shape is deliberate: two
    tasks calling ``send_json`` concurrently can interleave frames, and merging N
    queues by racing ``get()`` calls and cancelling the losers can swallow the
    event a cancelled ``Queue.get`` had already handed over. Forwarding costs one
    task per subscription and loses nothing.
    """

    def __init__(self, identity: SocketIdentity, hub: realtime.TopicHub) -> None:
        self.identity = identity
        self.hub = hub
        self.sink: asyncio.Queue[realtime.RealtimeEvent | None] = asyncio.Queue(
            maxsize=SOCKET_QUEUE_LIMIT
        )
        self.subs: dict[str, realtime.Subscription] = {}
        self._forwarders: dict[str, asyncio.Task[None]] = {}
        self.dropped = 0

    def attach(self, topic: str) -> None:
        """Subscribe to one of this workspace's topics and start forwarding it."""
        if topic in self.subs:
            return
        sub = self.hub.subscribe(self.identity.workspace_id, topic)
        self.subs[topic] = sub
        self._forwarders[topic] = asyncio.create_task(
            self._forward(sub), name=f"diq-stream-fwd-{topic}"
        )

    def detach(self, topic: str) -> asyncio.Task[None] | None:
        """Close the subscription and cancel its forwarder; returns that task."""
        sub = self.subs.pop(topic, None)
        if sub is not None:
            sub.close()
        forwarder = self._forwarders.pop(topic, None)
        if forwarder is not None:
            forwarder.cancel()
        return forwarder

    async def _forward(self, sub: realtime.Subscription) -> None:
        async for event in sub.events():
            if self.sink.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    self.sink.get_nowait()
                self.dropped += 1
            with contextlib.suppress(asyncio.QueueFull):
                self.sink.put_nowait(event)

    async def write(self, websocket: WebSocket) -> None:
        """The only task that touches the socket, until it is torn down."""
        while True:
            try:
                event = await asyncio.wait_for(self.sink.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                await websocket.send_json({"type": "heartbeat", "dropped": self.dropped})
                continue
            if event is None:
                return
            await websocket.send_json(event.to_wire())

    async def aclose(self) -> None:
        """Release every subscription, wait out its forwarder. Idempotent.

        Awaited rather than fire-and-forget: a cancelled task that is never
        joined is a task the event loop has to reap at an arbitrary point, and
        the teardown of a socket is exactly where a leaked subscription would
        start.
        """
        forwarders = [task for task in (self.detach(topic) for topic in list(self.subs)) if task]
        with contextlib.suppress(asyncio.QueueFull):
            self.sink.put_nowait(None)
        if forwarders:
            await asyncio.gather(*forwarders, return_exceptions=True)


# --------------------------------------------------------------------------- #
# Client frames
#
# Applied by a pure function of (socket, frame) so the rules are testable with
# no socket at all, and so the transport never grows a second opinion about what
# a frame means.
# --------------------------------------------------------------------------- #


async def _handle_frame(socket: _Socket, frame: Any) -> dict[str, Any] | None:
    """Apply one client frame; return the reply, or ``None`` to send nothing.

    Frame types:

    ``subscribe`` / ``unsubscribe``
        Change which of this workspace's topics the socket is attached to.
    ``demand``
        A reading reported by a client. It is published to the workspace and
        fanned out. Note what it is *not*: this transport does not write
        ``demand_observations``. Reconciliation — idempotent on
        ``(location_id, date)``, newer value wins, prior value kept — belongs to
        :func:`app.services.ingestion.normaliser.commit_demand` and to the
        device's HTTP ingest. A second writer here would be a second, competing
        conflict rule, so a socket report is a live observation and the
        reconciled row is still whatever ingestion decided.
    ``forecast``
        Always refused. Forecasts are server→device only
        (:data:`app.services.realtime.SERVER_ONLY_TOPICS`) — the asymmetry is the
        point, and the hub refuses it independently, so a caller that reaches
        the hub by another path cannot do it either.
    ``ping``
        Answered with ``pong``; a client may also simply wait for a heartbeat.
    """
    if not isinstance(frame, dict):
        return _error_frame("bad_frame", "Frame must be a JSON object")
    kind = frame.get("type")
    if not isinstance(kind, str):
        return _error_frame("bad_frame", "Frame is missing a string 'type'", field="type")

    if kind == "ping":
        return {"type": "pong", "at": datetime.datetime.now(datetime.UTC).isoformat()}

    if kind in {"subscribe", "unsubscribe"}:
        topic = frame.get("topic")
        if not isinstance(topic, str) or topic not in realtime.TOPICS:
            return _error_frame("unknown_topic", f"Unknown topic {topic!r}", field="topic")
        if kind == "subscribe":
            socket.attach(topic)
            return {"type": "subscribed", "topic": topic}
        socket.detach(topic)
        return {"type": "unsubscribed", "topic": topic}

    if kind == realtime.TOPIC_FORECAST:
        # Refused here as well as in the hub so the client learns why at its own
        # call site, and so the rule is testable without a broker.
        return _error_frame(
            "direction_not_allowed",
            "forecasts are server-to-device only: a client may read a forecast but never "
            "publish one, because a forecast carries model provenance the server owns",
            field="topic",
        )

    if kind == realtime.TOPIC_DEMAND:
        payload = frame.get("payload")
        if not isinstance(payload, dict):
            return _error_frame(
                "bad_frame", "Frame is missing an object 'payload'", field="payload"
            )
        if not socket.identity.may_publish_demand:
            return _error_frame("forbidden", "This client may not report demand", field="type")
        await socket.hub.publish(
            socket.identity.workspace_id,
            realtime.TOPIC_DEMAND,
            dict(payload),
            origin=realtime.PublishOrigin.DEVICE,
        )
        return None

    return _error_frame("bad_frame", f"Unsupported frame type {kind!r}", field="type")


def _error_frame(code: str, message: str, field: str | None = None) -> dict[str, Any]:
    return {"type": "error", "error": {"code": code, "message": message, "field": field}}


# --------------------------------------------------------------------------- #
# Route: health
# --------------------------------------------------------------------------- #


@router.get("/health", response_model=dict)
def stream_health() -> dict[str, Any]:
    """Report whether realtime is Redis-backed or degraded to this process.

    Unauthenticated on purpose, like ``/health``: the question it answers is
    "is this deployment's fan-out whole?", which an operator has to be able to
    ask while the fleet is down. So it exposes mode, reason, the delivery
    guarantee and subscriber counts — never a workspace id, a topic payload, or
    anything a tenant would recognise. A question about a specific tenant belongs
    to an authenticated client, which has the same facts in its ``hello`` frame.
    """
    body = realtime.realtime_status()
    body["timestamp"] = datetime.datetime.now(datetime.UTC).isoformat()
    body["status"] = "degraded" if body["mode"] == "degraded" else "ok"
    return body


# --------------------------------------------------------------------------- #
# Route: the socket
# --------------------------------------------------------------------------- #


@router.websocket("/ws")
async def stream_ws(websocket: WebSocket) -> None:
    """Hold an authenticated, workspace-scoped subscription open.

    On connect the client is attached to ``demand`` and ``forecast`` and may add
    or drop the rest with ``subscribe`` / ``unsubscribe`` frames. Both default
    topics are open to a device holding ``device:sync``, which is the only scope
    :data:`app.security.permissions.DEVICE_SCOPES` contains — a forecast is the
    server's claim about an uncertain future, and pushing it is the server's
    prerogative, so there is no device-side scope for a device to be denied on.

    Delivery is at-most-once with no replay: a reading missed while disconnected
    is in ``device_events``, and this socket only says that it changed.
    """
    credential = _extract_credential(websocket)
    if credential is None:
        await websocket.close(code=WS_UNAUTHENTICATED, reason="Authentication required")
        return
    payload: dict[str, Any] | None = None
    if credential.kind == "user":
        # Verified before the session is opened, so a bad signature costs
        # nothing but a signature check.
        try:
            payload = _authenticate_token(credential.secret)
        except _AuthError as exc:
            await websocket.close(code=exc.code, reason=exc.reason)
            return

    db = get_session_factory()()
    try:
        if credential.kind == "device":
            identity = _identity_from_device(db, credential.secret)
        elif payload is not None:
            identity = _identity_from_user(db, payload)
        else:  # pragma: no cover - the branch above returns for a bad token
            await websocket.close(code=WS_UNAUTHENTICATED, reason="Authentication required")
            return
    except _AuthError as exc:
        await websocket.close(code=exc.code, reason=exc.reason)
        return
    finally:
        db.close()

    await websocket.accept(subprotocol=credential.subprotocol or None)
    socket = _Socket(identity, realtime.get_hub())
    for topic in _DEFAULT_TOPICS:
        socket.attach(topic)
    try:
        await websocket.send_json(
            {
                "type": "hello",
                "identity": identity.as_wire(),
                "topics": sorted(socket.subs),
                "mode": realtime.realtime_mode(),
                "delivery": "at-most-once",
                "replay": False,
                "durable_source": "device_events",
                "buffer_limit": realtime.DEFAULT_BUFFER_LIMIT,
                "heartbeat_seconds": HEARTBEAT_SECONDS,
            }
        )
        await _pump(websocket, socket)
    except WebSocketDisconnect:
        logger.info("stream client disconnected subject=%s", identity.subject)
    finally:
        await socket.aclose()
        logger.info(
            "stream closed subject=%s workspace=%s", identity.subject, identity.workspace_id
        )


async def _pump(websocket: WebSocket, socket: _Socket) -> None:
    """Run the two directions concurrently until either one ends.

    Inbound applies client frames; outbound drains the sink. A disconnect kills
    both — the socket is gone, and a reader holding a queue nobody drains is
    exactly the leak this module is careful about elsewhere.
    """
    outbound = asyncio.create_task(socket.write(websocket), name="diq-stream-out")
    inbound = asyncio.create_task(_pump_in(websocket, socket), name="diq-stream-in")
    try:
        done, pending = await asyncio.wait({outbound, inbound}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        if pending:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*pending)
        for task in done:
            # Surface a failure that is not an ordinary disconnect, so a bug in
            # the frame handler is not mistaken for a client hanging up.
            task.result()
    finally:
        for task in (outbound, inbound):
            task.cancel()


async def _pump_in(websocket: WebSocket, socket: _Socket) -> None:
    """Apply client frames until the client goes away."""
    while True:
        reply = await _handle_frame(socket, await websocket.receive_json())
        if reply is not None:
            await websocket.send_json(reply)
