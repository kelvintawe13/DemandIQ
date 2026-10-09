"""Redis-backed shared state, with an in-process fallback.

Three pieces of the API keep state that must outlive a single request *and* be
visible to every worker:

* MFA login challenges (:mod:`app.routers.auth`) — a challenge issued by worker
  A must be verifiable by worker B, or MFA silently breaks behind a load
  balancer.
* agent quota/burst/session state (:mod:`app.routers.agent`).
* the auth rate limiter (:mod:`app.security.rate_limit`).

They used to be plain module-level dicts, so each worker kept its own copy: the
limits were per-process and every restart wiped them. This module gives them one
shared store.

The same "one worker must not hold a private copy" problem has a second shape:
a worker that accepts an ingest has to tell the *other* workers' connected
devices about it. :func:`channel`, :func:`get_pubsub` and :func:`publish_message`
are the broker half of that (consumed by :mod:`app.services.realtime`), kept here
so the optional-Redis idiom and the namespace live in one module rather than
being re-implemented — and slightly re-degraded — per consumer.

Degradation is deliberate and total: if Redis is not installed, not reachable,
or ``REDIS_URL`` is unset, :func:`get_redis` returns ``None`` and every consumer
falls back to its in-process structure. That path must keep working — it is what
the hermetic test suite runs on — so a missing Redis is a logged downgrade, never
an error.

:class:`SharedMap` is a write-through mapping rather than a thin Redis proxy, for
one pragmatic reason: the callers iterate their stores (``_SESSIONS.values()``,
``_USAGE.items()``) and mutate nested records in place. A proxy would turn those
into SCANs and lost writes. The contract is therefore:

* the **local copy is authoritative for reads and iteration** in the process
  that owns it, so existing behaviour is unchanged;
* every assignment or delete is **mirrored to Redis** under ``prefix``;
* a key missing locally is **read through** from Redis, so a second worker's
  writes are visible;
* iteration **unions** the local keys with a bounded SCAN of Redis, so a fresh
  process does not report a misleadingly partial view;
* in-place mutation of a nested value (``rec["turn_count"] += 1``) is *not*
  observable to the mirror — callers that do that must call :meth:`persist`.
"""

from __future__ import annotations

import datetime
import json
import logging
import uuid
from collections.abc import Iterator, MutableMapping
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

# Every key this application writes starts here, so a shared Redis database can
# be inspected (and flushed per-key) without guessing who owns what.
NAMESPACE = "diq"

_client: Any = None
_client_resolved = False
_client_available = False
# Why the last probe failed. Kept so an operator endpoint can *show* the
# degradation instead of leaving someone to infer it from events that never
# arrived; a logged downgrade nobody can read is a downgrade nobody fixes.
_degraded_reason: str | None = None


def get_redis() -> Any | None:
    """Return a live Redis client, or ``None`` when Redis cannot be used.

    Resolved at most once per process (``redis.Redis.from_url`` plus a ``PING``
    with sub-second timeouts), so this is safe to call on a hot path. Call
    :func:`reset_redis_cache` after changing ``REDIS_URL``.
    """
    global _client, _client_resolved, _client_available, _degraded_reason
    if _client_resolved:
        return _client if _client_available else None
    _client_resolved = True
    try:
        import redis  # imported lazily: the dependency is optional at runtime

        cfg = get_settings()
        client = redis.Redis.from_url(
            cfg.redis_url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            decode_responses=True,
        )
        client.ping()
    except Exception as exc:  # noqa: BLE001 - any failure means "no shared state"
        logger.info("Redis unavailable (%s); using in-process state", exc)
        _client_available = False
        _degraded_reason = str(exc)
        return None
    _client = client
    _client_available = True
    _degraded_reason = None
    logger.info("Redis-backed shared state enabled")
    return _client


def redis_enabled() -> bool:
    """True when a shared store is in use (as opposed to the local fallback)."""
    return get_redis() is not None


def redis_degraded_reason() -> str | None:
    """Why Redis is unusable, or ``None`` when it is usable.

    ``None`` is also the answer *before* the first probe, so a caller that needs
    to distinguish "working" from "not yet checked" should call
    :func:`get_redis` first — which every one of these consumers does.
    """
    return _degraded_reason


def reset_redis_cache() -> None:
    """Forget the resolved client so the next call re-probes Redis."""
    global _client, _client_resolved, _client_available, _degraded_reason
    _client = None
    _client_resolved = False
    _client_available = False
    _degraded_reason = None


# --------------------------------------------------------------------------- #
# Pub/sub
#
# Pub/sub is a different kind of shared state from the maps above: there is no key
# to read back, nothing to cache locally, and nothing worth mirroring, so
# :class:`SharedMap` is the wrong shape for it and is not reused. What the two do
# share is the degradation contract — a missing Redis means the caller falls
# back to an in-process hub, never an exception on a request that merely wanted
# to tell somebody something. Every function here returns a value that says
# "that did not happen" rather than raising.
# --------------------------------------------------------------------------- #


def channel(*parts: str) -> str:
    """Build a namespaced Redis channel name — ``diq:<part>:<part>…``.

    The same namespace as the keys above, so a shared Redis database can be read
    (and flushed per prefix) without guessing which subsystem owns a channel.
    """
    return ":".join((NAMESPACE, *parts))


def get_pubsub() -> Any | None:
    """A Redis pub/sub connection, or ``None`` when Redis cannot be used.

    A pub/sub connection is a *separate* socket from the command client and is
    in subscribe mode while it lives, so it is handed out per caller rather than
    shared: one subscriber per connection is what Redis expects, and two
    consumers on one connection split the stream between them.
    """
    client = get_redis()
    if client is None:
        return None
    try:
        return client.pubsub()
    except Exception as exc:  # noqa: BLE001 - a broker that will not subscribe
        logger.info("Redis pub/sub unavailable (%s); realtime stays in-process", exc)
        return None


def publish_message(channel_name: str, message: str) -> bool:
    """Publish ``message`` on ``channel_name``; True when Redis accepted it.

    False means "nobody was told", which the caller is expected to survive: a
    notify is never allowed to fail the write that produced it.
    """
    client = get_redis()
    if client is None:
        return False
    try:
        client.publish(channel_name, message)
        return True
    except Exception as exc:  # noqa: BLE001 - the local fan-out already happened
        logger.warning("could not publish to %s: %s", channel_name, exc)
        return False


# --------------------------------------------------------------------------- #
# Tagged JSON codec
#
# The shared records carry ``uuid.UUID`` and ``datetime`` values, and the agent
# session record is compared as ``started_at < cutoff`` — so those two types must
# survive a round-trip as themselves. Tagging them (rather than sniffing strings
# for something date-shaped) keeps a plain string field from being silently
# converted into a datetime.
# --------------------------------------------------------------------------- #

_TAG_UUID = "__uuid__"
_TAG_DATETIME = "__dt__"


def _encode(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return {_TAG_UUID: str(value)}
    if isinstance(value, datetime.datetime):
        return {_TAG_DATETIME: value.isoformat()}
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if len(value) == 1:
            if _TAG_UUID in value:
                try:
                    return uuid.UUID(str(value[_TAG_UUID]))
                except ValueError:
                    return value[_TAG_UUID]
            if _TAG_DATETIME in value:
                try:
                    return datetime.datetime.fromisoformat(str(value[_TAG_DATETIME]))
                except ValueError:
                    return value[_TAG_DATETIME]
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def encode_value(value: Any) -> str:
    """Serialise a shared value to JSON, preserving UUIDs and datetimes."""
    return json.dumps(_encode(value), separators=(",", ":"))


def decode_value(raw: str) -> Any:
    """Inverse of :func:`encode_value`."""
    return _decode(json.loads(raw))


class SharedMap(MutableMapping[Any, Any]):
    """A mapping mirrored to Redis, backed by an authoritative local copy.

    Args:
        name: Namespace below :data:`NAMESPACE`; Redis keys are ``diq:<name>:<k>``.
        ttl_seconds: Expiry applied to every key written to Redis. The local
            copy is never expired — the process that owns it is short-lived
            relative to the TTLs used here, and expiring it would change
            in-process semantics for no benefit.
        key_encode: How a key becomes a Redis key fragment (``str`` for UUIDs,
            ``"<uuid>|<date>"`` for the usage counters).
        key_decode: Inverse of ``key_encode``, used for keys discovered by SCAN.
        max_scan_batches: Bound on the SCAN cost of one iteration. Redis has no
            "list my keys" primitive, so a full view costs a scan; capping the
            batches keeps a hot iteration from walking an unbounded keyspace.
    """

    def __init__(
        self,
        name: str,
        *,
        ttl_seconds: int,
        key_encode: Any = str,
        key_decode: Any = None,
        max_scan_batches: int = 10,
    ) -> None:
        self.name = name
        self.ttl_seconds = ttl_seconds
        self._key_encode = key_encode
        self._key_decode = key_decode
        self._max_scan_batches = max_scan_batches
        self._data: dict[Any, Any] = {}

    # -- Redis plumbing ---------------------------------------------------- #

    @property
    def prefix(self) -> str:
        return f"{NAMESPACE}:{self.name}"

    def _redis_key(self, key: Any) -> str:
        return f"{self.prefix}:{self._key_encode(key)}"

    def _decode_key(self, fragment: str) -> Any:
        if self._key_decode is None:
            return fragment
        return self._key_decode(fragment)

    def _remote_keys(self) -> list[str]:
        """Key fragments Redis currently holds for this map (bounded SCAN)."""
        client = get_redis()
        if client is None:
            return []
        found: list[str] = []
        cursor: Any = 0
        try:
            for _ in range(self._max_scan_batches):
                cursor, batch = client.scan(cursor=cursor, match=f"{self.prefix}:*", count=200)
                found.extend(batch)
                if cursor == 0 or cursor == "0":
                    break
        except Exception as exc:  # noqa: BLE001 - iteration must not fail a request
            logger.debug("could not scan %s: %s", self.prefix, exc)
        base = f"{self.prefix}:"
        return [k[len(base) :] if k.startswith(base) else k for k in found]

    def _fetch(self, key: Any) -> Any:
        client = get_redis()
        if client is None:
            return _MISSING
        try:
            raw = client.get(self._redis_key(key))
        except Exception as exc:  # noqa: BLE001
            logger.debug("could not read %s: %s", self._redis_key(key), exc)
            return _MISSING
        if raw is None:
            return _MISSING
        return decode_value(raw)

    def _mirror(self, key: Any, value: Any) -> None:
        client = get_redis()
        if client is None:
            return
        try:
            client.set(self._redis_key(key), encode_value(value), ex=self.ttl_seconds)
        except Exception as exc:  # noqa: BLE001 - the local copy is still correct
            logger.warning("could not mirror %s to Redis: %s", self._redis_key(key), exc)

    def persist(self, key: Any) -> None:
        """Re-publish ``key`` after its value was mutated in place.

        ``rec["turn_count"] += 1`` cannot be observed by a mapping, so callers
        that mutate a nested value must say so explicitly.
        """
        if key in self._data:
            self._mirror(key, self._data[key])

    # -- MutableMapping ---------------------------------------------------- #

    def __getitem__(self, key: Any) -> Any:
        if key in self._data:
            return self._data[key]
        value = self._fetch(key)
        if value is _MISSING:
            raise KeyError(key)
        self._data[key] = value
        return value

    def __setitem__(self, key: Any, value: Any) -> None:
        self._data[key] = value
        self._mirror(key, value)

    def __delitem__(self, key: Any) -> None:
        # The mirror must be told even when the key is cached locally: a local
        # hit says nothing about whether Redis holds a copy, and skipping the
        # delete leaves a tombstone that a fresh worker reads back as live.
        cached = key in self._data
        self._data.pop(key, None)
        removed_remote = False
        client = get_redis()
        if client is not None:
            try:
                removed_remote = bool(client.delete(self._redis_key(key)))
            except Exception as exc:  # noqa: BLE001
                logger.debug("could not delete %s: %s", self._redis_key(key), exc)
        if not cached and not removed_remote:
            raise KeyError(key)

    def __iter__(self) -> Iterator[Any]:
        seen = dict.fromkeys(self._data)
        for fragment in self._remote_keys():
            key = self._decode_key(fragment)
            seen.setdefault(key, None)
        return iter(list(seen))

    def __len__(self) -> int:
        return len(list(iter(self)))

    def clear(self) -> None:
        """Drop both the local copy and every mirrored key."""
        self._data.clear()
        client = get_redis()
        if client is None:
            return
        try:
            keys = client.keys(f"{self.prefix}:*")
            if keys:
                client.delete(*keys)
        except Exception as exc:  # noqa: BLE001
            logger.debug("could not clear %s: %s", self.prefix, exc)


class _Missing:
    """Sentinel distinguishing "absent" from a stored ``None``."""


_MISSING = _Missing()
