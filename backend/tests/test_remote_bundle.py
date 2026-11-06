"""``MODEL_BUNDLE_URL``: the model service's volume, with the image as fallback.

The API fetches the bundle once at startup and then loads it like any other
export. Two properties matter and are pinned here:

* a reachable source is served, so a published retrain reaches forecasts without
  rebuilding the API image;
* an unreachable or invalid source never changes what the API answers — it falls
  back to the export bundled with the image, and to the documented stub when
  there is none. A model-service restart must not take forecasting down.
"""

from __future__ import annotations

import functools
import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import joblib
import pytest

from app.config import get_settings
from app.services.forecasting import bundle as bundle_mod
from app.services.forecasting.bundle import serving_status


def _write_bundle(path: Path, version: str = "remote-1") -> Path:
    """A minimal bundle: the loader validates format and version, nothing else."""
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "format": bundle_mod.BUNDLE_FORMAT,
            "bundle_version": bundle_mod.BUNDLE_VERSION,
            "created_at": "2026-10-01T00:00:00Z",
            "model": {
                "algorithm": "lightgbm",
                "version": version,
                "feature_set_version": "fs-test",
            },
        },
        path,
    )
    return path


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:  # noqa: D102 - silence test output
        pass


class _Source:
    """The URL and how many times it was asked for."""

    def __init__(self, url: str, counter: list[int]) -> None:
        self.url = url
        self._counter = counter

    @property
    def hits(self) -> int:
        return self._counter[0]


@pytest.fixture
def served(tmp_path: Path) -> Iterator[_Source]:
    """A directory served over HTTP, standing in for the model service."""
    root = tmp_path / "served"
    root.mkdir()
    _write_bundle(root / "serving_bundle.joblib")
    counter = [0]

    class _Counting(_Quiet):
        def do_GET(self) -> None:  # noqa: D102 - count, then serve
            counter[0] += 1
            super().do_GET()

    handler = functools.partial(_Counting, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield _Source(f"http://127.0.0.1:{httpd.server_address[1]}/serving_bundle.joblib", counter)
    finally:
        httpd.shutdown()
        httpd.server_close()


def _isolate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    url: str | None,
    shipped: Path | None = None,
    path: Path | None = None,
) -> Path:
    """Point the loader at ``url``, optionally with a path and a shipped fallback."""
    cache = tmp_path / "cache" / "serving_bundle.joblib"
    monkeypatch.setattr(get_settings(), "model_bundle_path", str(path) if path else None)
    monkeypatch.setattr(get_settings(), "model_bundle_url", url)
    monkeypatch.setattr(bundle_mod, "REMOTE_BUNDLE_CACHE", cache)
    monkeypatch.setattr(
        bundle_mod,
        "_DEFAULT_BUNDLE_CANDIDATES",
        (str(shipped),) if shipped is not None else (),
    )
    bundle_mod.clear_bundle_cache()
    return cache


def test_the_served_bundle_is_fetched_and_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, served: _Source
) -> None:
    cache = _isolate(monkeypatch, tmp_path, served.url)

    status = serving_status()

    assert status["mode"] == "trained"
    assert cache.exists()
    assert status["path"] == str(cache)
    assert status["algorithm"] == "lightgbm"
    assert status["version"] == "remote-1"


def test_an_unreachable_source_falls_back_to_the_shipped_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Port 9 on the loopback interface is closed, so the fetch fails fast.
    shipped = _write_bundle(tmp_path / "image" / "serving_bundle.joblib", version="shipped-1")
    _isolate(monkeypatch, tmp_path, "http://127.0.0.1:9/serving_bundle.joblib", shipped)

    status = serving_status()

    assert status["mode"] == "trained"
    assert status["path"] == str(shipped)
    assert status["version"] == "shipped-1"


def test_an_unreachable_source_with_no_fallback_is_the_stub(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Nothing is served and nothing is shipped: the API degrades, it does not fail.
    _isolate(monkeypatch, tmp_path, "http://127.0.0.1:9/serving_bundle.joblib")

    status = serving_status()

    assert status["mode"] == "stub"
    assert serving_status()["path"] is None


def test_a_stale_cache_is_not_served_when_the_source_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A cache file left by an earlier process is not a bundle this process
    # fetched. Serving it would answer from a model whose source we cannot reach
    # and call it "trained from the volume".
    _write_bundle(tmp_path / "cache" / "serving_bundle.joblib", version="stale-1")
    shipped = _write_bundle(tmp_path / "image" / "serving_bundle.joblib", version="shipped-1")
    _isolate(monkeypatch, tmp_path, "http://127.0.0.1:9/serving_bundle.joblib", shipped)

    status = serving_status()

    assert status["path"] == str(shipped)
    assert status["version"] == "shipped-1"


def test_a_bad_download_is_rejected_and_discarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "served"
    root.mkdir()
    (root / "serving_bundle.joblib").write_bytes(b"this is not a joblib file")
    handler = functools.partial(_Quiet, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/serving_bundle.joblib"
    try:
        shipped = _write_bundle(tmp_path / "image" / "serving_bundle.joblib", version="shipped-1")
        cache = _isolate(monkeypatch, tmp_path, url, shipped)

        status = serving_status()

        assert status["path"] == str(shipped)
        assert status["version"] == "shipped-1"
        # The rejected file must not sit in the cache pretending to be a bundle.
        assert not cache.exists()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_served_source_wins_over_the_path_in_the_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, served: _Source
) -> None:
    # This is the deployed shape: MODEL_BUNDLE_URL (the volume) and
    # MODEL_BUNDLE_PATH (the export baked into the image) are both set.
    shipped = _write_bundle(tmp_path / "image" / "serving_bundle.joblib", version="shipped-1")
    cache = _isolate(monkeypatch, tmp_path, served.url, shipped=shipped, path=shipped)

    status = serving_status()

    assert status["mode"] == "trained"
    assert status["path"] == str(cache)
    assert status["version"] == "remote-1"


def test_a_stale_path_does_not_fail_the_healthcheck_when_the_source_serves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, served: _Source
) -> None:
    # A path that is no longer loadable must not be reported as a configuration
    # error while the model service is answering: the volume is the live source.
    missing = tmp_path / "image" / "gone.joblib"
    _isolate(monkeypatch, tmp_path, served.url, path=missing)

    status = serving_status()

    assert status["mode"] == "trained"
    assert status["reason"] is None


def test_the_fetch_happens_once_per_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, served: _Source
) -> None:
    cache = _isolate(monkeypatch, tmp_path, served.url)

    # Resolution runs on every forecast, so the network is touched exactly once
    # and later resolves read the cached file.
    for _ in range(3):
        assert serving_status()["mode"] == "trained"
        assert serving_status()["path"] == str(cache)

    assert served.hits == 1
