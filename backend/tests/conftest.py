"""Shared pytest fixtures.

Hermetic settings: the suite must behave identically on CI (no ``backend/.env``,
no Redis, no training artifacts) and on developer machines that carry real
Turnstile/Google credentials, a running Redis, and a built serving bundle. Each
test starts with human-verification disabled; tests that exercise enforcement
set their own values via ``monkeypatch`` after this fixture runs (monkeypatch
undoes everything on teardown).

The two optional services are pinned OFF here rather than left to discovery:

* **Redis** — a developer's Redis is reachable, CI has none. Without pinning,
  the rate limiter and the shared agent/MFA state would silently exercise the
  Redis path locally and the in-process path on CI. Tests that specifically
  cover the shared path opt in with the ``redis_backend`` fixture.
* **the serving bundle** — ``model/artifacts/`` is gitignored, so a developer who
  has trained a model has ``serving_bundle.joblib`` on disk while CI does not,
  and ``get_bundle()`` probes for it. Every forecast test would then take the
  booster path locally and the stub path on CI. ``test_real_serving.py`` opts
  back in explicitly.
"""

import pytest

from app.config import get_settings
from app.services import kv


@pytest.fixture(autouse=True)
def _hermetic_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "turnstile_secret", None)
    monkeypatch.setattr(settings, "google_oauth_client_id", None)
    monkeypatch.setattr(settings, "google_oauth_client_secret", None)


@pytest.fixture(autouse=True)
def _no_redis_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep shared-state consumers on their in-process fallback.

    Patched at the module attribute so consumers calling ``kv.get_redis()`` see
    it, and reset here rather than per test because the resolution is cached.
    """
    kv.reset_redis_cache()
    monkeypatch.setattr(kv, "get_redis", lambda: None)
    yield
    kv.reset_redis_cache()


@pytest.fixture(autouse=True)
def _no_serving_bundle_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep forecast tests on the documented stub path.

    ``test_real_serving.py`` overrides ``model_bundle_path`` itself, and the
    default-discovery test restores the candidate list explicitly. The path is
    pinned to ``None`` as well, so a developer whose ``backend/.env`` sets
    ``MODEL_BUNDLE_PATH`` to a moved file cannot make the startup/health serving
    check fail the suite — the same reason Redis and the trained artifact are
    pinned off above.
    """
    from app.services.forecasting import bundle as bundle_mod

    monkeypatch.setattr(get_settings(), "model_bundle_path", None)
    monkeypatch.setattr(bundle_mod, "_DEFAULT_BUNDLE_CANDIDATES", ())
    bundle_mod.clear_bundle_cache()
    yield
    bundle_mod.clear_bundle_cache()
