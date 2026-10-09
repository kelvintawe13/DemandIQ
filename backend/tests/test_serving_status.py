"""The serving-mode assertion: a configured bundle that will not load must fail.

An *unset* ``MODEL_BUNDLE_PATH`` is the documented stub and must keep working.
A *set* path that does not load is a configuration error — the operator said
this deployment serves the trained model, and answering with rolling means
anyway is the silent failure this pins down. These tests avoid lightgbm/pandas
on purpose: the error and stub paths need neither.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from app.services.forecasting import bundle as bundle_mod
from app.services.forecasting.bundle import (
    ServingConfigurationError,
    assert_serving_configuration,
    serving_status,
)


def _isolate(monkeypatch: pytest.MonkeyPatch, configured: str | None) -> None:
    monkeypatch.setattr(get_settings(), "model_bundle_path", configured)
    monkeypatch.setattr(bundle_mod, "_DEFAULT_BUNDLE_CANDIDATES", ())
    bundle_mod.clear_bundle_cache()


def test_an_unset_path_is_the_documented_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, None)

    status = serving_status()
    assert status["mode"] == "stub"
    assert status["configured"] is False
    # The legitimate degradation must not raise.
    assert_serving_configuration()


def test_a_configured_path_that_does_not_exist_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    bad = str(tmp_path / "not-a-bundle.joblib")
    _isolate(monkeypatch, bad)

    status = serving_status()
    assert status["mode"] == "error"
    assert status["configured"] is True
    assert bad in status["reason"]

    with pytest.raises(ServingConfigurationError):
        assert_serving_configuration()


def test_a_configured_file_that_is_not_a_bundle_is_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    junk = tmp_path / "junk.joblib"
    junk.write_text("this is not a serving bundle")
    _isolate(monkeypatch, str(junk))

    assert serving_status()["mode"] == "error"
    with pytest.raises(ServingConfigurationError):
        assert_serving_configuration()


def test_health_reports_a_bad_configuration_as_503(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _isolate(monkeypatch, str(tmp_path / "missing.joblib"))

    # Constructed without `with`, so the lifespan (which would refuse to boot)
    # does not run here — this is the runtime safety net for a process that is
    # already up when the bundle disappears.
    resp = TestClient(create_app()).get("/api/v1/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["serving"]["mode"] == "error"


def test_health_is_ok_on_the_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, None)

    resp = TestClient(create_app()).get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["serving"]["mode"] == "stub"


def test_the_api_refuses_to_boot_on_a_bad_path(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Entering the context manager runs the lifespan — the real boot path.

    ``test_health_reports_a_bad_configuration_as_503`` above constructs the
    client *without* entering it, which covers a process that is already up when
    the bundle goes away. This is the other half: a fresh start. It must raise,
    because coming up and quietly answering every forecast with rolling means is
    the exact failure this check exists to prevent.
    """
    _isolate(monkeypatch, str(tmp_path / "missing.joblib"))

    with pytest.raises(ServingConfigurationError, match="MODEL_BUNDLE_PATH"):
        with TestClient(create_app()):
            pass


def test_the_api_boots_and_serves_health_on_the_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The assertion must not cost the documented stub deployment its boot."""
    _isolate(monkeypatch, None)

    with TestClient(create_app()) as client:
        resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["serving"]["mode"] == "stub"


def test_the_job_worker_refuses_to_start_on_a_bad_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The worker runs forecasts too, so it shares the startup check."""
    _isolate(monkeypatch, str(tmp_path / "missing.joblib"))

    from app.jobs import worker

    with pytest.raises(ServingConfigurationError):
        worker.main(["--once"])
