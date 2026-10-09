"""Shared-state tests: the Redis mirror, the rate limiter, and MFA challenges.

The suite runs on the in-process fallback by default (``tests/conftest.py``
pins ``kv.get_redis`` to ``None``), because CI has no Redis and a developer
usually does. The tests that exercise the *shared* path put a real client back
and skip when none is reachable — so they are evidence on a machine with Redis
and an honest skip without one, never a green run that checked nothing.
"""

from __future__ import annotations

import datetime
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

import app.services.kv as kv
from app.security import rate_limit

# Captured at import time, BEFORE the autouse conftest fixture replaces the
# module attribute. This is the only handle on the real probe.
_REAL_GET_REDIS = kv.get_redis


@pytest.fixture
def redis_backend(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Restore the real Redis client for the duration of one test."""
    kv.reset_redis_cache()
    client = _REAL_GET_REDIS()
    if client is None:
        pytest.skip("Redis is not reachable; the shared path cannot be exercised")
    monkeypatch.setattr(kv, "get_redis", lambda: client)
    yield client
    kv.reset_redis_cache()


class _Request:
    """Minimal stand-in for a Starlette request (client key + request id)."""

    def __init__(self, peer: str = "10.0.0.7", headers: dict[str, str] | None = None) -> None:
        self.client = SimpleNamespace(host=peer)
        self.headers = headers or {}


def _unique(name: str) -> str:
    return f"{name}:{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------- #
# Fallback behaviour (no Redis)
# --------------------------------------------------------------------------- #


def test_shared_map_behaves_like_a_dict_without_redis() -> None:
    """The local copy IS the store when no Redis is available."""
    m = kv.SharedMap(_unique("t:mem"), ttl_seconds=60)
    assert len(m) == 0
    m["a"] = 1
    m["b"] = 2
    assert m["a"] == 1
    assert sorted(m) == ["a", "b"]
    assert sorted(m.keys()) == ["a", "b"]
    assert dict(m.items()) == {"a": 1, "b": 2}
    assert m.get("missing") is None
    assert m.get("missing", "dflt") == "dflt"
    assert "a" in m
    assert m.pop("a") == 1
    del m["b"]
    assert len(m) == 0
    m["c"] = 3
    m.clear()
    assert len(m) == 0
    with pytest.raises(KeyError):
        _ = m["c"]


def test_shared_map_persist_is_a_noop_without_redis() -> None:
    """``persist`` must not explode when there is nothing to mirror to."""
    m = kv.SharedMap(_unique("t:persist"), ttl_seconds=60)
    m["a"] = {"n": 1}
    m["a"]["n"] = 2  # in-place edit
    m.persist("a")
    m.persist("never-set")
    assert m["a"]["n"] == 2


def test_codec_round_trips_uuids_and_datetimes() -> None:
    """Tagged encoding: a datetime comes back a datetime, a string a string."""
    payload = {
        "id": uuid.uuid4(),
        "when": datetime.datetime(2026, 3, 4, 18, 0, tzinfo=datetime.UTC),
        "ended_at": None,
        "count": 3,
        "label": "2026-03-04T18:00:00+00:00",  # a string that LOOKS like a datetime
        "nested": [{"at": datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)}],
    }
    back = kv.decode_value(kv.encode_value(payload))
    assert isinstance(back["id"], uuid.UUID)
    assert back["id"] == payload["id"]
    assert isinstance(back["when"], datetime.datetime)
    assert back["when"] == payload["when"]
    assert back["label"] == payload["label"]  # not coerced to a datetime
    assert back["ended_at"] is None
    assert back["count"] == 3
    assert isinstance(back["nested"][0]["at"], datetime.datetime)


# --------------------------------------------------------------------------- #
# The shared path (real Redis)
# --------------------------------------------------------------------------- #


def test_shared_map_is_visible_to_a_second_worker(redis_backend: Any) -> None:
    """What one worker writes, another reads — the whole point of the change."""
    name = _unique("t:cross")
    worker_a = kv.SharedMap(name, ttl_seconds=60)
    worker_b = kv.SharedMap(name, ttl_seconds=60)
    session_id = uuid.uuid4()
    record = {
        "id": session_id,
        "user_id": uuid.uuid4(),
        "started_at": datetime.datetime(2026, 3, 4, 18, 0, tzinfo=datetime.UTC),
        "ended_at": None,
        "turn_count": 0,
    }
    worker_a[session_id] = record

    # B has never seen this key locally; it must come from Redis, with types.
    assert worker_b[session_id]["turn_count"] == 0
    assert isinstance(worker_b[session_id]["started_at"], datetime.datetime)
    assert worker_b[session_id]["user_id"] == record["user_id"]
    # Iteration is unioned with Redis, so a fresh process is not blind.
    assert session_id in list(worker_b)
    worker_a.clear()


def test_shared_map_survives_a_fresh_worker_after_clear(redis_backend: Any) -> None:
    """``clear`` reaches Redis, not just the local copy."""
    name = _unique("t:clear")
    a = kv.SharedMap(name, ttl_seconds=60)
    a["k"] = "v"
    a.clear()
    fresh = kv.SharedMap(name, ttl_seconds=60)
    assert len(fresh) == 0
    with pytest.raises(KeyError):
        _ = fresh["k"]


def test_shared_map_writes_carry_a_ttl(redis_backend: Any) -> None:
    """Every mirrored key expires, so abandoned state cannot accumulate."""
    name = _unique("t:ttl")
    m = kv.SharedMap(name, ttl_seconds=90)
    m["k"] = {"n": 1}
    ttl = redis_backend.ttl(m._redis_key("k"))
    assert 0 < ttl <= 90
    m.clear()


def test_tuple_keys_round_trip_through_redis(redis_backend: Any) -> None:
    """The usage counters are keyed ``(user_id, date)``; both halves must return."""
    from app.routers.agent import _usage_key, _usage_key_decode

    name = _unique("t:usage")
    m = kv.SharedMap(name, ttl_seconds=60, key_encode=_usage_key, key_decode=_usage_key_decode)
    user_id = uuid.uuid4()
    m[(user_id, "2026-09-25")] = 4

    other = kv.SharedMap(name, ttl_seconds=60, key_encode=_usage_key, key_decode=_usage_key_decode)
    assert other[(user_id, "2026-09-25")] == 4
    # Discovered by SCAN, so the key decodes back to the tuple the caller used.
    assert (user_id, "2026-09-25") in list(other)
    m.clear()


# --------------------------------------------------------------------------- #
# Rate limiting
# --------------------------------------------------------------------------- #


def test_rate_limit_allows_max_then_blocks() -> None:
    """Unchanged contract (in-process): the 6th call in the window is refused."""
    prefix = _unique("rl")
    req = _Request()
    for _ in range(5):
        rate_limit.check_rate_limit(req, max_requests=5, window_seconds=60, key_prefix=prefix)
    with pytest.raises(HTTPException) as exc:
        rate_limit.check_rate_limit(req, max_requests=5, window_seconds=60, key_prefix=prefix)
    assert exc.value.status_code == 429
    assert exc.value.detail["error"]["code"] == "rate_limited"


def test_rate_limit_is_shared_across_workers_on_redis(redis_backend: Any) -> None:
    """One client's window is the deployment's window, not one worker's."""
    prefix = _unique("rl")
    first_worker = _Request()
    second_worker = _Request()  # same peer address, different process in reality
    for _ in range(5):
        rate_limit.check_rate_limit(
            first_worker, max_requests=5, window_seconds=60, key_prefix=prefix
        )
    with pytest.raises(HTTPException):
        rate_limit.check_rate_limit(
            second_worker, max_requests=5, window_seconds=60, key_prefix=prefix
        )


def test_rate_limit_ignores_forwarded_for_by_default() -> None:
    """X-Forwarded-For is caller-supplied: it must not open a fresh bucket."""
    prefix = _unique("rl")
    for i in range(5):
        rate_limit.check_rate_limit(
            _Request(headers={"x-forwarded-for": f"203.0.113.{i}"}),
            max_requests=5,
            window_seconds=60,
            key_prefix=prefix,
        )
    # A brand-new claimed address, same peer -> same bucket -> refusal.
    with pytest.raises(HTTPException):
        rate_limit.check_rate_limit(
            _Request(headers={"x-forwarded-for": "203.0.113.99"}),
            max_requests=5,
            window_seconds=60,
            key_prefix=prefix,
        )


def test_rate_limit_honours_forwarded_for_when_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Behind a proxy that rewrites the header, distinct clients get distinct buckets."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "trusted_proxy_headers", True)
    prefix = _unique("rl")
    for i in range(5):
        rate_limit.check_rate_limit(
            _Request(headers={"x-forwarded-for": f"198.51.100.{i}"}),
            max_requests=5,
            window_seconds=60,
            key_prefix=prefix,
        )
    # A different forwarded address is a different client, so it is unaffected.
    rate_limit.check_rate_limit(
        _Request(headers={"x-forwarded-for": "198.51.100.200"}),
        max_requests=5,
        window_seconds=60,
        key_prefix=prefix,
    )


# --------------------------------------------------------------------------- #
# MFA challenges
# --------------------------------------------------------------------------- #


def test_mfa_challenges_are_bounded_by_a_ttl() -> None:
    """A TTl replaces the old unbounded dict that only a success ever pruned."""
    from app.routers.auth import MFA_CHALLENGE_TTL_SECONDS, _mfa_challenges

    assert MFA_CHALLENGE_TTL_SECONDS == 600
    assert _mfa_challenges.ttl_seconds == MFA_CHALLENGE_TTL_SECONDS


def test_mfa_challenge_issued_by_one_worker_verifies_on_another(redis_backend: Any) -> None:
    """The bug this closes: /login and /mfa/verify landing on different workers."""
    from app.routers.auth import MFA_CHALLENGE_TTL_SECONDS, _mfa_challenges

    worker_one = _mfa_challenges
    # A second process reading the same namespace.
    worker_two = kv.SharedMap(
        worker_one.name, ttl_seconds=MFA_CHALLENGE_TTL_SECONDS, key_decode=None
    )
    challenge_id = str(uuid.uuid4())
    user_id = uuid.uuid4()
    worker_one[challenge_id] = user_id

    assert worker_two[challenge_id] == user_id
    assert isinstance(worker_two[challenge_id], uuid.UUID)
    # Consuming the challenge removes it for everyone.
    worker_one.pop(challenge_id, None)
    fresh = kv.SharedMap(worker_one.name, ttl_seconds=MFA_CHALLENGE_TTL_SECONDS)
    with pytest.raises(KeyError):
        _ = fresh[challenge_id]
