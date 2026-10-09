"""Training artifacts and checkpoints.

Every training stage writes a checkpoint before moving on, so a Colab
session can die (runtime recycle, disconnect, quota) without losing work.
On resume, completed stages are loaded and skipped, and only remaining
stages run.

Design rules:
- writes are atomic: temp file + rename, so a killed process never leaves
  a half-written artifact behind;
- every artifact carries a manifest (stage, timestamps, durations, seed,
  library versions, row counts) so any number can be traced later;
- the manifest records data checksums, per the reproducibility requirements
  in MODEL_LIFECYCLE.md.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import math
import os
import platform
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import joblib
import pandas as pd

logger = logging.getLogger(__name__)

# Packages recorded in every stage manifest. Missing ones are skipped so the
# manifest works both locally and on Colab where the mix differs.
_TRACKED_PACKAGES = (
    "pandas",
    "numpy",
    "scikit-learn",
    "lightgbm",
    "catboost",
    "xgboost",
    "joblib",
)


def _library_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in _TRACKED_PACKAGES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            continue
    return versions


def sha256_of_path(path: Path) -> str:
    """SHA-256 hex digest of any file on disk (provenance for source data)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256(path: Path) -> str:
    return sha256_of_path(path)


def _atomic_write(path: Path, writer: Any) -> None:
    """Write via a temp file in the same directory, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            writer(f)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@dataclass
class StageManifest:
    """Provenance record for one completed training stage."""

    stage: str
    started_at: str
    finished_at: str
    duration_s: float
    seed: int
    python_version: str
    platform: str
    versions: dict[str, str]
    data: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


class CheckpointStore:
    """Filesystem-backed store of stage checkpoints.

    Layout::

        <root>/
          manifest.json            # index of completed stages
          visits.parquet           # stage output example
          <stage>.joblib           # stage payload (model, frame, ...)
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.json"

    # ------------------------------------------------------------- manifest
    def _read_manifest(self) -> dict[str, Any]:
        if self.manifest_path.exists():
            with open(self.manifest_path) as f:
                return json.load(f)
        return {"stages": {}}

    def _write_manifest(self, manifest: dict[str, Any]) -> None:
        payload = json.dumps(manifest, indent=2, default=str).encode("utf-8")
        _atomic_write(self.manifest_path, lambda f: f.write(payload))

    def completed_stages(self) -> list[str]:
        return sorted(self._read_manifest().get("stages", {}).keys())

    def stage_info(self, stage: str) -> dict[str, Any] | None:
        return self._read_manifest().get("stages", {}).get(stage)

    def stage_metric(self, stage: str, key: str) -> Any:
        info = self.stage_info(stage)
        if info is None:
            return None
        return info.get("metrics", {}).get(key)

    # ------------------------------------------------------------ artifacts
    def has(self, name: str) -> bool:
        return (self.root / name).exists()

    def save_dataframe(self, df: pd.DataFrame, name: str) -> Path:
        path = self.root / name
        df.to_parquet(path)
        return path

    def load_dataframe(self, name: str) -> pd.DataFrame:
        return pd.read_parquet(self.root / name)

    def save_object(self, obj: Any, name: str) -> Path:
        path = self.root / name
        _atomic_write(path, lambda f: joblib.dump(obj, f))
        return path

    def save_text(self, text: str, name: str) -> Path:
        """Write a text artifact (e.g. a markdown table) atomically."""
        path = self.root / name
        _atomic_write(path, lambda f: f.write(text.encode("utf-8")))
        return path

    def load_object(self, name: str) -> Any:
        with open(self.root / name, "rb") as f:
            return joblib.load(f)

    # ----------------------------------------------------------- stage flow
    def record_stage(
        self,
        stage: str,
        started: float,
        seed: int,
        data: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> StageManifest:
        """Record a completed stage with full provenance."""
        if not (isinstance(started, (int, float)) and math.isfinite(started) and started > 0):
            # An implausible ``started`` (typically 0 from an ad-hoc caller)
            # would otherwise land in the manifest as a 1970 timestamp and a
            # duration of ~the current epoch. Degrade to 'now' and say so.
            logger.warning(
                "record_stage: implausible started=%r for stage '%s'; using now",
                started,
                stage,
            )
            started = time.time()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        manifest = self._read_manifest()
        manifest["stages"][stage] = asdict(
            StageManifest(
                stage=stage,
                started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                finished_at=now,
                duration_s=round(time.time() - started, 3),
                seed=seed,
                python_version=platform.python_version(),
                platform=platform.platform(),
                versions=_library_versions(),
                data=data or {},
                metrics=metrics or {},
            )
        )
        self._write_manifest(manifest)
        logger.info("Stage '%s' recorded (%.1fs)", stage, time.time() - started)
        return StageManifest(**manifest["stages"][stage])

    def sha256_of(self, name: str) -> str | None:
        path = self.root / name
        return sha256_of_path(path) if path.exists() else None
