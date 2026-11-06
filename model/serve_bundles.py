#!/usr/bin/env python3
"""Host exported serving bundles for the API.

The API normally loads the export baked into its own image. This service is the
alternative source: it owns a volume, so a bundle produced by a retrain can be
published once and served to the API, and every export that was ever published
stays on the volume.

Endpoints
    GET  /healthz                       liveness, plus what is on the volume
    GET  /latest/serving_bundle.joblib  the current export (what the API fetches)
    GET  /bundles                       JSON index of stored exports
    GET  /bundles/<file>                one stored export
    POST /bundles                       publish an export and make it latest

``POST /bundles`` needs ``X-Bundle-Token: <BUNDLE_ADMIN_TOKEN>``. Anything that
can reach this service on the private network can otherwise replace the model
the API serves, so uploads are refused outright when the token is unset.

Validation is the same contract the API enforces on load: a joblib mapping with
``format == "demandiq-serving-bundle"`` and a ``bundle_version`` it understands.
A file that fails it is rejected before it reaches the volume, because the API
treats an unloadable bundle at a configured path as a configuration error.

Environment
    PORT                 listen port (default 9000)
    BUNDLES_DIR          volume mount (default /data/bundles)
    BUNDLE_ADMIN_TOKEN   enables POST /bundles when set
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import io
import json
import logging
import os
import re
import signal
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

BUNDLE_FORMAT = "demandiq-serving-bundle"
BUNDLE_VERSION = 1
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")

LOG = logging.getLogger("serve_bundles")


def bundles_dir() -> Path:
    return Path(os.environ.get("BUNDLES_DIR", "/data/bundles"))


def latest_path() -> Path:
    return bundles_dir() / "latest" / "serving_bundle.joblib"


def exports_dir() -> Path:
    return bundles_dir() / "exports"


def _ensure_layout() -> None:
    latest_path().parent.mkdir(parents=True, exist_ok=True)
    exports_dir().mkdir(parents=True, exist_ok=True)


def validate_bundle(body: bytes) -> dict[str, Any]:
    """Return the bundle's metadata, or raise ValueError if it is not one.

    The checks mirror the API loader so a rejected upload can never become a
    bundle that fails at startup.
    """
    try:
        import joblib  # part of the base dependencies, imported lazily
    except ImportError as exc:  # pragma: no cover - image always installs it
        raise ValueError("joblib is not installed in this image") from exc
    try:
        bundle = joblib.load(io.BytesIO(body))
    except Exception as exc:
        raise ValueError(f"not a joblib file: {exc}") from exc
    if not isinstance(bundle, dict) or bundle.get("format") != BUNDLE_FORMAT:
        raise ValueError(f"not a {BUNDLE_FORMAT} export")
    version = int(bundle.get("bundle_version", 0) or 0)
    if version < BUNDLE_VERSION:
        raise ValueError(
            f"bundle_version {version} is older than the API serves ({BUNDLE_VERSION})"
        )
    model = bundle.get("model") or {}
    return {
        "bundle_version": version,
        "algorithm": model.get("algorithm"),
        "version": model.get("version"),
        "feature_set_version": model.get("feature_set_version"),
        "created_at": bundle.get("created_at"),
    }


def publish(body: bytes, meta: dict[str, Any]) -> dict[str, Any]:
    """Store the export under exports/ and atomically replace latest/."""
    _ensure_layout()
    digest = hashlib.sha256(body).hexdigest()
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    name = f"{stamp}-{digest[:8]}.joblib"
    stored = exports_dir() / name
    stored.write_bytes(body)

    # Write to a sibling first so a reader never sees a partial bundle.
    tmp = latest_path().with_suffix(".joblib.tmp")
    tmp.write_bytes(body)
    os.replace(tmp, latest_path())

    return {"file": name, "sha256": digest, "bytes": len(body), **meta}


def index() -> list[dict[str, Any]]:
    entries = []
    for path in sorted(exports_dir().glob("*.joblib"), reverse=True):
        stat = path.stat()
        entries.append(
            {
                "file": path.name,
                "bytes": stat.st_size,
                "stored_at": datetime.datetime.fromtimestamp(
                    stat.st_mtime, datetime.UTC
                ).isoformat(),
            }
        )
    return entries


class Handler(BaseHTTPRequestHandler):
    server_version = "demandiq-bundles"
    protocol_version = "HTTP/1.1"

    # -- helpers ----------------------------------------------------------
    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _file(self, path: Path) -> None:
        try:
            stat = path.stat()
            handle = path.open("rb")
        except OSError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "no bundle published yet"})
            return

        etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'
        if self.headers.get("If-None-Match") == etag:
            handle.close()
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(stat.st_size))
        self.send_header("ETag", etag)
        self.send_header(
            "Last-Modified",
            self.date_time_string(stat.st_mtime),
        )
        self.end_headers()
        with handle:
            if self.command != "HEAD":
                while chunk := handle.read(1024 * 256):
                    self.wfile.write(chunk)

    def _token_ok(self) -> bool:
        expected = os.environ.get("BUNDLE_ADMIN_TOKEN", "")
        if not expected:
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": "uploads are disabled: BUNDLE_ADMIN_TOKEN is not set"},
            )
            return False
        supplied = self.headers.get("X-Bundle-Token", "")
        if not hmac.compare_digest(supplied, expected):
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "invalid bundle token"})
            return False
        return True

    # -- routes -----------------------------------------------------------
    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path).rstrip("/") or "/"
        if path == "/healthz":
            latest = latest_path()
            if not latest.exists():
                self._json(
                    HTTPStatus.OK,
                    {"status": "ok", "latest": None, "exports": len(index())},
                )
                return
            stat = latest.stat()
            self._json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "latest": {
                        "bytes": stat.st_size,
                        "changed_at": datetime.datetime.fromtimestamp(
                            stat.st_mtime, datetime.UTC
                        ).isoformat(),
                    },
                    "exports": len(index()),
                },
            )
            return
        if path in ("/latest/serving_bundle.joblib", "/latest"):
            self._file(latest_path())
            return
        if path == "/bundles":
            self._json(HTTPStatus.OK, {"latest": latest_path().name, "exports": index()})
            return
        if path.startswith("/bundles/"):
            name = path[len("/bundles/") :]
            if not SAFE_NAME.match(name) or not name.endswith(".joblib"):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid bundle name"})
                return
            self._file(exports_dir() / name)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    do_HEAD = do_GET

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path).rstrip("/")
        if path != "/bundles":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self._token_ok():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "empty body"})
            return
        if length > MAX_UPLOAD_BYTES:
            self._json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": f"bundle larger than {MAX_UPLOAD_BYTES} bytes"},
            )
            return
        body = self.rfile.read(length)
        try:
            meta = validate_bundle(body)
        except ValueError as exc:
            LOG.warning("rejected upload: %s", exc)
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
            return
        published = publish(body, meta)
        LOG.info(
            "published %s (%s bytes) algorithm=%s version=%s",
            published["file"],
            published["bytes"],
            published["algorithm"],
            published["version"],
        )
        self._json(HTTPStatus.CREATED, published)

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    _ensure_layout()
    port = int(os.environ.get("PORT", "9000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True

    latest = latest_path()
    if latest.exists():
        LOG.info("serving %s (%s bytes) on :%d", latest, latest.stat().st_size, port)
    else:
        LOG.warning(
            "no bundle on the volume yet; :%d is up, uploads enabled=%s",
            port,
            bool(os.environ.get("BUNDLE_ADMIN_TOKEN")),
        )
    LOG.info("bundles dir %s, %d stored export(s)", bundles_dir(), len(index()))

    def _stop(signum: int, _frame: Any) -> None:
        LOG.info("signal %s: shutting down", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
