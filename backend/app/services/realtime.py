"""Workspace-scoped realtime fan-out for the device fleet (Layer 4).

A till that ingests a demand reading and a dashboard that is watching the same
location should not need a poll to agree about it, so an accepted ingest
publishes an event and every connected client of *that workspace* sees it. This
module is the hub that does the fanning out, and the two rules that make it safe
to point at production data.

**Tenant isolation is structural, not a filter.** The registry is keyed by
``(workspace_id, topic)`` and :meth:`TopicHub.publish` looks up exactly one key.
There is no code path that iterates "all topics and check the workspace
afterwards", so a missing workspace check cannot become a cross-tenant leak by
omission — the wrong key simply reaches nobody.

**Delivery is at-most-once, and this is not replayable.** A pub/sub message is
handed to whoever is connected at that instant. A subscriber that is
disconnected, slow, or was started a millisecond late has no copy of it, and
this hub keeps no log, no cursor and no resume token. Treating the stream as
durable would mean telling a device "you have seen demand 41" and being unable
to prove it. So: anything that must be *recoverable* is written to
``device_events`` — the append-only table in :mod:`app.models.device` — and the
stream is a low-latency notification that the table has moved. A consumer that
needs replay reads ``device_events`` (or its own domain table) and uses the
socket only to learn *that* something changed. That is the whole contract; it is
stated here rather than in a README because the tempting future feature — "add
resumption" — is a different product.

**Degradation is total and silent-by-default.** With no Redis, the hub keeps
subscribers in this process and events reach only this process's subscribers.
Multi-worker fan-out is lost, nothing else is. :func:`realtime_status` exists so
an operator can see that rather than infer it from a device that quietly stopped
receiving forecasts; ``GET /api/v1/stream/health`` serves it.

**Buffering is capped, per subscriber.** A client that stops reading cannot make
the process grow without bound: past :data:`DEFAULT_BUFFER_LIMIT` the *oldest*
queued event is dropped to make room. Oldest-first eviction is the right side to
drop here because the domain rule is already last-writer-wins
(:func:`app.services.ingestion.normaliser.commit_demand` reconciles on
``(location_id, date)`` and keeps the newer value), so a device that catches up
after a stall wants the latest value, not a queue of values it has already been
superseded by. Drops are counted on the subscription and reported in the
``dropped`` field of the ``hello``/``stats`` frames — this hub does not pretend
to be a log.

**Direction is asymmetric on purpose.** Demand is bidirectional: the server
publishes an accepted reading, and a device may report one. Forecasts are
server→device only. A forecast is a claim about an uncertain future that
carries the model bundle and an ``as_of`` timestamp; one minted on a till has
provenance nobody can stand behind, and accepting one would let a compromised
device write "today's demand is 900" into another operator's screen.
:func:`TopicHub.publish` therefore refuses ``origin=device`` on
:data:`SERVER_ONLY_TOPICS` — in the hub, not only in the transport, so a caller
that is not the WebSocket cannot route around it.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.services import kv

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Vocabulary
#
# A closed set, not free strings: the transport, the health frame and the
# direction rule all switch on these, and a typo would otherwise create a topic
# that is publishable but that nothing ever subscribes to.
# --------------------------------------------------------------------------- #

TOPIC_DEMAND = "demand"
TOPIC_FORECAST = "forecast"
TOPIC_DEVICE = "device"
TOPIC_ALERT = "alert"

TOPICS: tuple[str, ...] = (TOPIC_DEMAND, TOPIC_FORECAST, TOPIC_DEVICE, TOPIC_ALERT)

#: Topics a device may read but never write. See the module docstring for why
#: forecasts are in here and demand is not.
SERVER_ONLY_TOPICS: frozenset[str] = frozenset({TOPIC_FORECAST})

#: Redis channel prefix — ``<prefix>:<workspace_id>:<topic>``. The workspace is
#: in the channel name as well as in the local registry key, so the broker
#: itself separates tenants: a subscriber pattern subscription for one workspace
#: never sees another workspace's bytes, and a misconfigured consumer is
#: contained by the broker rather than by our own bookkeeping.
CHANNEL_PREFIX = f"{kv.NAMESPACE}:rt"

#: Messages held for one subscriber before the oldest is discarded. Sized for a
#: burst of demand events across a fleet, not for a backlog: a subscriber that
#: falls this far behind is better served by re-reading ``device_events`` than
#: by a growing queue. See the module docstring.
DEFAULT_BUFFER_LIMIT = 256

#: Identifies this process in the Redis envelope. The publishing worker delivers
#: locally first and then publishes; the bridge must therefore ignore its own
#: messages, or every event would arrive twice on the worker that made it.
_INSTANCE_ID = uuid.uuid4().hex


class PublishOrigin(StrEnum):
    """Who is publishing — the only input the direction rule reads."""

    SERVER = "server"
    DEVICE = "device"


class DirectionError(ValueError):
    """A device tried to publish on a server→device-only topic."""

    def __init__(self, topic: str) -> None:
        super().__init__(
            f"topic {topic!r} is server-to-device only; a device may report demand "
            "but cannot publish forecasts, which carry model provenance the server owns"
        )
        self.topic = topic


# --------------------------------------------------------------------------- #
# Event
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RealtimeEvent:
    """One message on the wire.

    ``event_id`` is generated, not sequential, and is not a cursor: it lets a
    client correlate a push with a log line, nothing more. Ordering within a
    topic is the order publishes completed on one worker, and no more than that
    across workers.
    """

    event_id: str
    workspace_id: uuid.UUID
    topic: str
    payload: dict[str, Any]
    published_at: datetime.datetime

    def to_wire(self) -> dict[str, Any]:
        """JSON-safe form for the WebSocket frame."""
        return {
            "event_id": self.event_id,
            "workspace_id": str(self.workspace_id),
            "topic": self.topic,
            "published_at": self.published_at.isoformat(),
            "payload": self.payload,
        }

    def to_envelope(self) -> dict[str, Any]:
        """Broker form — UUIDs and datetimes survive via the ``kv`` codec."""
        return {
            "event_id": self.event_id,
            "workspace_id": self.workspace_id,
            "topic": self.topic,
            "payload": self.payload,
            "published_at": self.published_at,
        }

    @classmethod
    def from_envelope(cls, data: dict[str, Any]) -> RealtimeEvent:
        """Inverse of :meth:`to_envelope`, after :func:`app.services.kv.decode_value`."""
        return cls(
            event_id=str(data["event_id"]),
            workspace_id=uuid.UUID(str(data["workspace_id"])),
            topic=str(data["topic"]),
            payload=dict(data.get("payload") or {}),
            published_at=data["published_at"],
        )


def channel_name(workspace_id: uuid.UUID, topic: str) -> str:
    """The Redis channel a workspace's topic travels on."""
    return kv.channel("rt", str(workspace_id), topic)


# --------------------------------------------------------------------------- #
# Subscription
# --------------------------------------------------------------------------- #


class Subscription:
    """One client's view of one ``(workspace, topic)`` pair.

    Holds a bounded queue and nothing else — no history, no replay. Obtain one
    from :meth:`TopicHub.subscribe` and close it when you are done; it is an async
    context manager so the close cannot be skipped by an early return.
    """

    def __init__(self, hub: TopicHub, workspace_id: uuid.UUID, topic: str, *, limit: int) -> None:
        if limit < 1:
            raise ValueError("subscription buffer limit must be at least 1")
        self._hub = hub
        self.workspace_id = workspace_id
        self.topic = topic
        self.limit = limit
        self.dropped = 0
        self.closed = False
        # ``None`` is the wake-up sentinel: a consumer parked in ``get()`` with no
        # timeout has to be released by close(), and it cannot tell a sentinel
        # from an event because an event is never None.
        self._queue: asyncio.Queue[RealtimeEvent | None] = asyncio.Queue(maxsize=limit)

    # -- producer side (the hub) ------------------------------------------- #

    def offer(self, event: RealtimeEvent) -> bool:
        """Queue ``event``; evict the oldest if the buffer is full.

        Returns False once closed, so a fan-out does not have to check first.
        """
        if self.closed:
            return False
        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            pass
        with contextlib.suppress(asyncio.QueueEmpty):
            self._queue.get_nowait()
        self.dropped += 1
        # Safe without a suppress: exactly one item was removed from a queue of
        # at least one, and there is no await between the two statements, so no
        # other producer can have refilled it.
        self._queue.put_nowait(event)
        return True

    # -- consumer side ------------------------------------------------------ #

    async def get(self, timeout: float | None = None) -> RealtimeEvent | None:
        """Next event, or ``None`` on timeout / after close.

        ``timeout`` is what lets a transport interleave a heartbeat and a
        disconnect check; ``None`` parks until something arrives.
        """
        if self.closed:
            return None
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except TimeoutError:
            return None

    async def events(self, *, heartbeat: float | None = None) -> AsyncIterator[RealtimeEvent]:
        """Iterate events until closed.

        ``heartbeat`` is a max idle wait after which the generator simply loops —
        it yields nothing, it just re-checks ``closed`` so a cancelled consumer
        terminates promptly instead of holding a registry entry.
        """
        while not self.closed:
            event = await self.get(timeout=heartbeat)
            if event is None:
                continue
            yield event

    def __aiter__(self) -> AsyncIterator[RealtimeEvent]:
        return self.events()

    # -- lifecycle ---------------------------------------------------------- #

    def close(self) -> None:
        """Detach from the hub and release the registry entry. Idempotent."""
        if self.closed:
            return
        self.closed = True
        self._hub.forget(self)
        # Best effort: a full buffer cannot take the sentinel, and a consumer
        # then learns about the close on its next timeout instead.
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)

    async def __aenter__(self) -> Subscription:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# Hub
# --------------------------------------------------------------------------- #


class TopicHub:
    """Workspace-scoped topic registry and fan-out point.

    One instance per process. The default is module-level (:func:`get_hub`) so a
    router can publish without threading a hub through every call site; a test
    builds its own for isolation.
    """

    def __init__(self, *, buffer_limit: int = DEFAULT_BUFFER_LIMIT) -> None:
        self._topics: dict[tuple[uuid.UUID, str], set[Subscription]] = {}
        self._buffer_limit = buffer_limit
        self._bridge: asyncio.Task[None] | None = None
        self._bridging = False

    # -- registry ----------------------------------------------------------- #

    def subscribe(
        self, workspace_id: uuid.UUID, topic: str, *, limit: int | None = None
    ) -> Subscription:
        """Register a subscriber for one workspace's topic and start it.

        Synchronous and immediate: the subscription exists the moment this
        returns, so a publish issued on the next line cannot miss it.
        """
        sub = Subscription(
            self,
            workspace_id,
            topic,
            limit=self._buffer_limit if limit is None else limit,
        )
        self._topics.setdefault((workspace_id, topic), set()).add(sub)
        self._ensure_bridge()
        return sub

    def forget(self, sub: Subscription) -> None:
        """Drop ``sub``, and the key itself when it was the last subscriber.

        Without the second half a long-lived process accumulates one empty set per
        topic per workspace it has ever served, which is exactly the unbounded
        growth the buffer cap exists to prevent — the cap bounds bytes, this
        bounds entries.
        """
        key = (sub.workspace_id, sub.topic)
        holders = self._topics.get(key)
        if holders is None:
            return
        holders.discard(sub)
        if not holders:
            del self._topics[key]

    def subscriber_count(
        self, workspace_id: uuid.UUID | None = None, topic: str | None = None
    ) -> int:
        """Total live subscribers, or the count for one workspace/topic pair."""
        if workspace_id is None and topic is None:
            return sum(len(holders) for holders in self._topics.values())
        if workspace_id is None or topic is None:
            raise ValueError("filter by workspace_id and topic together, or by neither")
        return len(self._topics.get((workspace_id, topic), ()))

    def active_topics(self) -> list[dict[str, Any]]:
        """One entry per occupied key — the registry, for the health frame."""
        return [
            {
                "workspace_id": str(key[0]),
                "topic": key[1],
                "subscribers": len(holders),
            }
            for key, holders in sorted(
                self._topics.items(), key=lambda item: (str(item[0][0]), item[0][1])
            )
        ]

    # -- publish ------------------------------------------------------------ #

    async def publish(
        self,
        workspace_id: uuid.UUID,
        topic: str,
        payload: dict[str, Any],
        *,
        origin: PublishOrigin = PublishOrigin.SERVER,
    ) -> RealtimeEvent:
        """Fan an event out to one workspace's subscribers of ``topic``.

        Local delivery first, then the broker. A broker failure is logged and
        swallowed: the writers that call this (an accepted ingest, a committed
        forecast) have already succeeded, and failing them because a notification
        did not go out would be worse than a device that misses one.

        Raises:
            DirectionError: A device asked to publish a server-only topic.
        """
        if origin is PublishOrigin.DEVICE and topic in SERVER_ONLY_TOPICS:
            logger.warning(
                "refused device-originated publish topic=%s workspace=%s", topic, workspace_id
            )
            raise DirectionError(topic)

        event = RealtimeEvent(
            event_id=str(uuid.uuid4()),
            workspace_id=workspace_id,
            topic=topic,
            payload=payload,
            published_at=datetime.datetime.now(datetime.UTC),
        )
        self._fan_out(event)
        await self._fan_out_across_workers(event)
        return event

    def _fan_out(self, event: RealtimeEvent) -> None:
        """Deliver to the local subscribers of exactly this key.

        One dict lookup, and the key carries the workspace — there is no
        post-hoc workspace test to forget. The tuple snapshot keeps a subscriber
        that closes itself mid-fan-out from mutating the set being walked.
        """
        for sub in tuple(self._topics.get((event.workspace_id, event.topic), ())):
            sub.offer(event)

    async def _fan_out_across_workers(self, event: RealtimeEvent) -> None:
        if kv.get_redis() is None:
            return
        envelope = kv.encode_value({"worker": _INSTANCE_ID, "event": event.to_envelope()})
        # The client is synchronous, so the blocking PUBLISH goes to a thread
        # rather than stalling the event loop for every connected device.
        await asyncio.to_thread(
            kv.publish_message, channel_name(event.workspace_id, event.topic), envelope
        )

    # -- broker bridge ------------------------------------------------------ #

    def _ensure_bridge(self) -> None:
        """Start the Redis listener on the first local subscriber, once.

        Only the receiving side needs the bridge, so this is called from
        ``subscribe``. With no Redis it is a no-op, which is why the hermetic
        suite starts no thread and no task.
        """
        if self._bridging or kv.get_pubsub() is None:
            return
        if self._bridge is not None and not self._bridge.done():
            return
        self._bridging = True
        self._bridge = asyncio.create_task(self._bridge_loop(), name="diq-realtime-bridge")
        logger.info("Redis-backed realtime fan-out enabled")

    async def _bridge_loop(self) -> None:
        """Read one pattern subscription and deliver what other workers sent."""
        pubsub = kv.get_pubsub()
        if pubsub is None:
            return
        pattern = f"{CHANNEL_PREFIX}:*"
        try:
            await asyncio.to_thread(pubsub.psubscribe, pattern)
        except Exception as exc:  # noqa: BLE001 - stay on the in-process hub
            logger.warning("could not subscribe to %s: %s; realtime is in-process", pattern, exc)
            return
        try:
            while True:
                try:
                    # ``get_message`` blocks for the timeout, so it cannot run
                    # on the loop; the timeout is what lets the task notice it
                    # was cancelled and unwind.
                    message = await asyncio.to_thread(pubsub.get_message, timeout=1.0)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - a dead broker ends the bridge
                    logger.warning("realtime bridge read failed: %s; back to in-process", exc)
                    return
                if message is None:
                    continue
                self._absorb(message)
        finally:
            with contextlib.suppress(Exception):
                pubsub.close()
            self._bridging = False

    def _absorb(self, message: dict[str, Any]) -> None:
        """Turn one broker message into local deliveries."""
        if message.get("type") not in {"message", "pmessage"}:
            return
        raw = message.get("data")
        if not isinstance(raw, (str, bytes)):
            return
        try:
            envelope = kv.decode_value(raw.decode() if isinstance(raw, bytes) else raw)
        except Exception as exc:  # noqa: BLE001 - a foreign publisher on our namespace
            logger.debug("ignoring unreadable realtime envelope: %s", exc)
            return
        if not isinstance(envelope, dict) or envelope.get("worker") == _INSTANCE_ID:
            # Our own publish already went out through ``_fan_out``.
            return
        data = envelope.get("event")
        if not isinstance(data, dict):
            return
        try:
            self._fan_out(RealtimeEvent.from_envelope(data))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("ignoring malformed realtime envelope: %s", exc)

    # -- lifecycle ---------------------------------------------------------- #

    async def shutdown(self) -> None:
        """Close every subscription and stop the bridge. Used at shutdown and by tests."""
        for sub in [s for holders in self._topics.values() for s in holders]:
            sub.close()
        self._topics.clear()
        bridge = self._bridge
        self._bridge = None
        self._bridging = False
        if bridge is not None and not bridge.done():
            bridge.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await bridge


# --------------------------------------------------------------------------- #
# Module-level default hub
# --------------------------------------------------------------------------- #

_default_hub = TopicHub()


def get_hub() -> TopicHub:
    """The process-wide hub. A router that needs its own builds a :class:`TopicHub`."""
    return _default_hub


async def publish(
    workspace_id: uuid.UUID,
    topic: str,
    payload: dict[str, Any],
    *,
    origin: PublishOrigin = PublishOrigin.SERVER,
) -> RealtimeEvent:
    """Publish on the default hub.

    Await it from an ``async def`` route. A *synchronous* route runs in a
    threadpool with no loop of its own, so there use
    ``asyncio.run(realtime.publish(...))`` — never call it from inside a
    coroutine that is already running.
    """
    return await _default_hub.publish(workspace_id, topic, payload, origin=origin)


def subscribe(workspace_id: uuid.UUID, topic: str, *, limit: int | None = None) -> Subscription:
    """Open a subscription on the default hub. Close it when you are done."""
    return _default_hub.subscribe(workspace_id, topic, limit=limit)


def realtime_mode() -> str:
    """``"redis"`` when the broker is carrying fan-out, else ``"degraded"``."""
    return "redis" if kv.get_redis() is not None else "degraded"


def realtime_status() -> dict[str, Any]:
    """Operator-facing snapshot of the hub.

    Deliberately repeats the delivery guarantee in the payload. An operator
    reading ``/api/v1/stream/health`` to answer "why did that device not get the
    forecast" should not have to know that the answer is in a docstring.
    """
    mode = realtime_mode()
    return {
        "mode": mode,
        "reason": kv.redis_degraded_reason() if mode == "degraded" else None,
        "delivery": "at-most-once",
        "replay": False,
        "durable_source": "device_events",
        "buffer_limit": DEFAULT_BUFFER_LIMIT,
        "topics": list(TOPICS),
        "server_only_topics": sorted(SERVER_ONLY_TOPICS),
        "subscribers": _default_hub.subscriber_count(),
        "active": _default_hub.active_topics(),
    }


async def reset_hub() -> None:
    """Tear the default hub down — test isolation and graceful shutdown."""
    await _default_hub.shutdown()
