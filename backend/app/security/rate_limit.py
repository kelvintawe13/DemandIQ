"""Rate limiting — Redis sliding window, in-process fallback.

The counter used to live in a module-level dict, which meant each worker enforced
its own limit and any restart reset every bucket. It now lives in Redis when one
is reachable (:mod:`app.services.kv`), so the limit a client is actually held to
is the one the deployment configured.

The in-process deque is kept as the fallback for environments without Redis
(local development, the hermetic test suite) and is still the structure
``_buckets`` exposes, because tests reset state through it.

Two properties matter more than the algorithm:

* the limit is applied to a **server-derived** client key. ``X-Forwarded-For`` is
  only honoured when ``TRUSTED_PROXY_HEADERS`` is on — otherwise any caller can
  pick a fresh bucket per request and the limit is decorative. Turn it on when
  the app genuinely sits behind a proxy that overwrites the header.
* a Redis outage downgrades to the per-process counter (still enforced, but not
  shared) rather than failing open.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable

from fastapi import HTTPException, Request

from app.config import get_settings

# Imported as a module, not as ``from app.services.kv import get_redis``: the
# availability probe is cached, and tests (and a Redis restart) monkeypatch
# ``kv.get_redis``. Binding the function here would freeze the answer at import.
from app.services import kv

logger = logging.getLogger(__name__)

# In-memory fallback — NOT shared between workers. It is what the hermetic test
# suite exercises; production traffic should run against Redis.
_buckets: dict[str, deque[float]] = defaultdict(deque)


def _client_key(request: Request) -> str:
    """The bucket key for a request.

    ``X-Forwarded-For`` is attacker-controlled unless a proxy rewrites it, so it
    is only consulted when ``TRUSTED_PROXY_HEADERS`` is enabled. The default
    keys on the peer address the ASGI server actually saw.
    """
    if get_settings().trusted_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


def _memory_window_hit(key: str, *, now: float, window_seconds: int, max_requests: int) -> bool:
    """Record a hit in the local deque. Returns True when it is over the limit."""
    bucket = _buckets[key]
    while bucket and bucket[0] <= now - window_seconds:
        bucket.popleft()
    if len(bucket) >= max_requests:
        return True
    bucket.append(now)
    # Reap buckets that have gone quiet, so a long-lived process does not keep
    # one deque per client address forever.
    if len(_buckets) > 1024:
        for stale in [k for k, b in _buckets.items() if not b or b[-1] <= now - window_seconds]:
            if stale != key:
                _buckets.pop(stale, None)
    return False


def _redis_window_hit(key: str, *, now: float, window_seconds: int, max_requests: int) -> bool:
    """Record a hit in a Redis sorted set. Returns True when over the limit.

    The set is scored by wall-clock time so buckets are shared across hosts (a
    monotonic clock is only comparable within one machine). Members are unique
    per hit — the score alone would silently collapse hits inside the same
    clock tick. ``max_requests`` is passed as a bound so a runaway client cannot
    grow the set between expiries.
    """
    client = kv.get_redis()
    if client is None:  # pragma: no cover - caller checks availability first
        return _memory_window_hit(
            key, now=now, window_seconds=window_seconds, max_requests=max_requests
        )
    redis_key = f"diq:ratelimit:{key}"
    try:
        pipe = client.pipeline()
        pipe.zremrangebyscore(redis_key, 0, now - window_seconds)
        pipe.zcard(redis_key)
        results = pipe.execute()
        used = int(results[1])
        if used >= max_requests:
            # Do not record the rejected hit: a throttled client must not be
            # able to keep its own bucket permanently at the limit.
            client.expire(redis_key, window_seconds + 1)
            return True
        client.zadd(redis_key, {f"{now:.6f}-{uuid.uuid4().hex[:8]}": now})
        client.expire(redis_key, window_seconds + 1)
        return False
    except Exception as exc:  # noqa: BLE001 - degrade to the local counter
        logger.warning("rate-limit Redis operation failed (%s); using in-process counter", exc)
        return _memory_window_hit(
            key, now=now, window_seconds=window_seconds, max_requests=max_requests
        )


def check_rate_limit(
    request: Request,
    *,
    max_requests: int = 20,
    window_seconds: int = 60,
    key_prefix: str = "global",
) -> None:
    """Enforce a sliding-window rate limit.

    Args:
        request: Incoming request (used to derive the client key).
        max_requests: Maximum requests per window.
        window_seconds: Window size in seconds.
        key_prefix: Bucket prefix to isolate different routes.

    Raises:
        HTTPException: 429 if the limit is exceeded.
    """
    key = f"{key_prefix}:{_client_key(request)}"
    now = time.time()
    if kv.get_redis() is not None:
        over = _redis_window_hit(
            key, now=now, window_seconds=window_seconds, max_requests=max_requests
        )
    else:
        over = _memory_window_hit(
            key, now=now, window_seconds=window_seconds, max_requests=max_requests
        )
    if over:
        logger.warning("Rate limit exceeded key=%s", key)
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": "rate_limited",
                    "message": "Too many requests. Please try again later.",
                    "field": None,
                    "reference": request.headers.get("x-request-id"),
                }
            },
        )


def rate_limit_dependency(
    max_requests: int = 10,
    window_seconds: int = 60,
    key_prefix: str = "auth",
) -> Callable[[Request], None]:
    """Factory returning a FastAPI dependency that rate-limits."""

    def _dep(request: Request) -> None:
        check_rate_limit(
            request,
            max_requests=max_requests,
            window_seconds=window_seconds,
            key_prefix=key_prefix,
        )

    return _dep
