#!/usr/bin/env python3
"""DemandIQ training entry point for the official Google Colab CLI.

Executed remotely from your terminal:

    colab new -s diq
    colab drivemount -s diq
    colab exec -s diq -f scripts/colab/train_remote.py
    colab stop -s diq

Or as a one-shot ephemeral job:

    colab run --keep scripts/colab/train_remote.py

Drive-first design: data and artifacts live in Drive, so `colab stop` never
destroys state - the same contract as the browser notebook. Without Drive
mounted, the script falls back to /tmp data + /content artifacts (env-var
driven) and prints the `colab download` commands to pull results back.

Configuration (checked in order):
    1. /content/diq_config.json   - uploaded by `make colab-train` from Makefile vars
    2. DIQ_* environment variables - only if your tooling forwards env to the VM
    3. Built-in defaults          - EDIT REPO_URL below for your fork

Config keys: repo_url, repo_ref, test_start, seed, stages (list),
data_root, ckpt_dir. Env equivalents: DIQ_REPO_URL, DIQ_REPO_REF,
DIQ_TEST_START, DIQ_SEED, DIQ_STAGES, DIQ_DATA_ROOT, DIQ_CKPT_DIR.

Kaggle credentials: kaggle.json in Drive at MyDrive/kaggle/kaggle.json, or
KAGGLE_USERNAME/KAGGLE_KEY set in the remote session.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Optional config uploaded by `make colab-train` (see Makefile).
_CFG_PATH = Path("/content/diq_config.json")
_CFG = json.loads(_CFG_PATH.read_text()) if _CFG_PATH.exists() else {}

def _cfg(key: str, env: str, default: str) -> str:
    return str(_CFG.get(key) or os.environ.get(env) or default)

REPO_URL = _cfg("repo_url", "DIQ_REPO_URL", "https://github.com/kelvintawe13/DemandIQ.git")  # EDIT
REPO_REF = _cfg("repo_ref", "DIQ_REPO_REF", "main")
TEST_START = _cfg("test_start", "DIQ_TEST_START", "2017-02-01")
SEED = int(_cfg("seed", "DIQ_SEED", "42"))
_stages = _CFG.get("stages") or [s for s in os.environ.get("DIQ_STAGES", "").split(",") if s]
STAGES = _stages or None

KAGGLE_COMPETITION = "recruit-restaurant-visitor-forecasting"
RECRUIT_CSVS = ("air_visit_data.csv", "air_store_info.csv", "date_info.csv", "air_reserve.csv")


def sh(cmd: str) -> None:
    print(f"$ {cmd}", flush=True)
    subprocess.run(cmd, shell=True, check=True)


def drive_root() -> Path | None:
    d = Path("/content/drive/MyDrive")
    return d if d.exists() else None


def ensure_kaggle_token(drive: Path | None) -> None:
    if Path.home().joinpath(".kaggle/kaggle.json").exists():
        return
    if os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY"):
        print("Using KAGGLE_USERNAME/KAGGLE_KEY from environment")
        return
    token = drive / "kaggle/kaggle.json" if drive else None
    assert token and token.exists(), (
        f"No kaggle credentials: put kaggle.json in Drive at {drive}/kaggle/kaggle.json, "
        "or export KAGGLE_USERNAME and KAGGLE_KEY before running."
    )
    Path.home().joinpath(".kaggle").mkdir(exist_ok=True)
    sh(f'cp "{token}" ~/.kaggle/kaggle.json && chmod 600 ~/.kaggle/kaggle.json')


def ensure_data(data_root: Path, drive: Path | None) -> None:
    if (data_root / RECRUIT_CSVS[0]).exists():
        print(f"Data already present: {data_root}")
        return
    ensure_kaggle_token(drive)
    data_root.mkdir(parents=True, exist_ok=True)
    sh(f"kaggle competitions download -c {KAGGLE_COMPETITION} -p /tmp/recruit")
    sh(f"unzip -o -q '/tmp/recruit/*.zip' -d '{data_root}'")
    sh(f"gunzip -f '{data_root}'/*.csv.gz 2>/dev/null || true")
    missing = [c for c in RECRUIT_CSVS if not (data_root / c).exists()]
    assert not missing, f"Download incomplete, missing: {missing}"
    print(f"Data ready: {data_root}")


def ensure_package() -> Path:
    repo = Path("/content/demandiq")
    if not repo.exists():
        sh(f'git clone "{REPO_URL}" "{repo}"')
    sh(f'cd "{repo}" && git fetch origin && git checkout "{REPO_REF}"')
    sh(f'cd "{repo}/model" && pip install -q -e . pyarrow tabulate 2>&1 | tail -1')
    sys.path.insert(0, str(repo / "model" / "src"))
    return repo


def main() -> int:
    drive = drive_root()
    print(f"Drive mounted: {drive or 'NO - using /tmp + /content (ephemeral mode)'}")

    data_root = Path(_cfg("data_root", "DIQ_DATA_ROOT", ""))
    ckpt_dir = Path(_cfg("ckpt_dir", "DIQ_CKPT_DIR", ""))
    if not data_root:
        data_root = (drive / "demandiq/data/recruit") if drive else Path("/tmp/recruit")
    if not ckpt_dir:
        ckpt_dir = (
            (drive / "demandiq/artifacts/colab-cli-run") if drive
            else Path("/content/artifacts/runs/colab-cli")
        )

    ensure_data(data_root, drive)
    ensure_package()

    from diq_model.artifacts import CheckpointStore
    from diq_model.train import run_pipeline

    store = CheckpointStore(ckpt_dir)
    completed = store.completed_stages()
    print(f"Completed stages so far: {completed or 'none'}")

    results = run_pipeline(
        store=store,
        data_root=data_root,
        test_start=TEST_START,
        seed=SEED,
        stages=STAGES,
    )

    print("\n=== Stage metrics ===")
    for stage, metrics in results.items():
        print(f"{stage:>10}: {metrics}")
    print(f"\nArtifacts in: {ckpt_dir}")
    print("Drive-backed: they survive `colab stop` and are visible at "
          "drive.google.com > demandiq/")
    if not drive:
        print("\nEphemeral mode - pull results back before stopping:")
        print(f"  colab download -s diq '{ckpt_dir}/manifest.json' ./manifest.json")
        print(f"  colab download -s diq '{ckpt_dir}' ./artifacts-run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
