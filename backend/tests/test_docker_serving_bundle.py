"""Guard the three halves of serving the trained model in the container stack.

The loader (:mod:`app.services.forecasting.bundle`) probes ``/model/...`` for the
compose mount, and ``docker-compose.yml`` is supposed to provide it. Nothing tied
those together, so the compose file could (and did) claim a mount that was never
declared: the container then has no copy of ``model/`` at all and the API serves
stub rolling-mean forecasts while looking perfectly healthy.

The third half is the healthcheck. ``/health`` is liveness and answers 200 no
matter what forecasting is doing, so a deployment whose configured bundle stopped
loading was still reported ``healthy``. The image probe has to hit the endpoint
that actually reflects the serving mode (``/api/v1/health``,
:mod:`app.routers.health`) or the container is green for the same reasons the old
one was.

This test reads the compose file and the Dockerfile — no Docker.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from app.services.forecasting.bundle import DEFAULT_BUNDLE_CANDIDATES

COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker-compose.yml"
DOCKERFILE_PATH = Path(__file__).resolve().parents[1] / "Dockerfile"
CONTAINER_MOUNT = "/model"
CONTAINER_BUNDLE = "/model/artifacts/runs/latest/serving_bundle.joblib"

# The probe URL inside the one HEALTHCHECK line, e.g. ``...8000/api/v1/health'``.
_PROBE_URL = re.compile(r"127\.0\.0\.1:8000(/[^']*)")


def _compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text())


def test_compose_mounts_the_directory_the_bundle_loader_probes() -> None:
    compose = _compose()
    # The mount lives on the shared fragment every API process uses (migrate,
    # backend, worker), so it is declared once and inherited.
    volumes = compose["x-api-common"].get("volumes") or []
    mounts = {str(v).replace(" ", "") for v in volumes}
    assert "./model:/model:ro" in mounts, (
        "docker-compose.yml no longer mounts ./model at /model, but "
        "app/services/forecasting/bundle.py still probes it — the API would "
        "silently serve stub forecasts. See DOCKER.md § Model serving."
    )


def test_the_loader_probes_the_mounted_path() -> None:
    # If the candidate list stops including the container mount, the compose
    # volume above becomes dead weight and real serving silently stops working.
    assert CONTAINER_BUNDLE in DEFAULT_BUNDLE_CANDIDATES
    assert any(path.startswith(CONTAINER_MOUNT) for path in DEFAULT_BUNDLE_CANDIDATES)


def test_compose_file_exists() -> None:
    assert COMPOSE_PATH.exists(), f"expected {COMPOSE_PATH}"


def _healthcheck_probe_url() -> str:
    # The HEALTHCHECK is one logical line continued with a backslash; join the
    # continuations before looking for the probe URL.
    text = DOCKERFILE_PATH.read_text().replace("\\\n", " ")
    for line in text.splitlines():
        if line.startswith("HEALTHCHECK"):
            match = _PROBE_URL.search(line)
            assert match, "the HEALTHCHECK declares no probe URL"
            return match.group(1)
    raise AssertionError("backend/Dockerfile declares no HEALTHCHECK")


def test_the_image_healthcheck_uses_the_serving_aware_endpoint() -> None:
    """``/health`` cannot see a degraded serving mode; this probe must.

    ``frontend`` waits on ``backend: condition: service_healthy``, so the URL
    below is the last line of defence: get it wrong and a deployment that has
    fallen back to rolling means still reports as a healthy container. See the
    Dockerfile comment and DOCKER.md § Model serving.
    """
    assert _healthcheck_probe_url() == "/api/v1/health", (
        "the API image healthcheck no longer probes /api/v1/health, the only "
        "health route that reports a degraded serving mode as 503 — a bad "
        "MODEL_BUNDLE_PATH would make the container look healthy again. See "
        "backend/app/routers/health.py."
    )
