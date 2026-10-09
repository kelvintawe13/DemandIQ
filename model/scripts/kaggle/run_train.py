#!/usr/bin/env python3
"""DemandIQ training entry point for a Kaggle kernel (payload mode).

No GitHub, no pip, no internet required. `kaggle kernels push` uploads only
the code file, so EVERYTHING the kernel needs rides in the attached dataset:

    1. `make kaggle-push` stages the model/ tree as payload/code.zip plus
       payload/commit.txt alongside the Recruit CSVs and versions the
       private dataset; the kernel attaches it.
    2. On the VM we extract every zip under /kaggle/input, locate the
       payload, put model/src on sys.path (pure-Python package; Kaggle's
       image already ships pandas/numpy/lightgbm/catboost/shap/sklearn/
       pyarrow), unpack the Recruit CSVs if they arrive zipped, and run
       the full checkpointed pipeline.
    3. Everything written under /kaggle/working comes back via
       `kaggle kernels output`.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

WORK = Path("/kaggle/working")
ARTIFACTS = WORK / "artifacts/runs/kaggle"
INPUT = Path("/kaggle/input")
EXTRACT = Path("/tmp/input-extract")
RECRUIT_CSVS = (
    "air_visit_data.csv",
    "air_store_info.csv",
    "date_info.csv",
    "air_reserve.csv",
)


def debug_input_tree() -> None:
    """Print the attached input tree - ground truth for payload debugging."""
    print("--- /kaggle/input tree ---", flush=True)
    for p in sorted(INPUT.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(INPUT)}  ({p.stat().st_size:,} B)", flush=True)
    print("--- end tree ---", flush=True)


def _extract_all_zips() -> None:
    """Datasets arrive as zip archives (dir-mode zip) or loose files; dig
    everything out into EXTRACT so the searches below see one flat tree."""
    EXTRACT.mkdir(exist_ok=True)
    for z in INPUT.rglob("*.zip"):
        with zipfile.ZipFile(z) as zf:
            zf.extractall(EXTRACT)


def unpack_payload() -> Path:
    """Locate the bundled model/ tree and return the project (model/) root.

    Kaggle auto-extracts zips inside datasets, so the model tree usually
    arrives as LOOSE files under .../payload/code/model/; a surviving
    code.zip is the fallback.
    """

    def is_project(p: Path) -> bool:
        return (p / "src" / "diq_model").is_dir()

    projects = [p for p in INPUT.rglob("pyproject.toml") if is_project(p.parent)]
    if not projects:
        pkg_root = WORK / "pkg"
        pkg_root.mkdir(exist_ok=True)
        for z in sorted(INPUT.rglob("code.zip")) + sorted(EXTRACT.rglob("code.zip")):
            with zipfile.ZipFile(z) as zf:
                zf.extractall(pkg_root)
        projects = [p for p in pkg_root.rglob("pyproject.toml") if is_project(p.parent)]
    assert projects, "model/ payload (pyproject.toml + src/diq_model) not found"
    project = projects[0].parent
    print("payload project root:", project)

    commits = sorted(INPUT.rglob("commit.txt")) or sorted(EXTRACT.rglob("commit.txt"))
    if commits:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        (ARTIFACTS / "source_commit.txt").write_text(commits[0].read_text())
        print("source commit:", commits[0].read_text().strip())
    return project


def prepare_data() -> Path:
    """Locate the four Recruit CSVs in the extracted input tree."""
    for root in (EXTRACT, INPUT):
        if all((root / c).exists() for c in RECRUIT_CSVS):
            return root
        for cand in sorted(root.rglob("air_visit_data.csv")):
            if all((cand.parent / c).exists() for c in RECRUIT_CSVS):
                return cand.parent
    raise SystemExit("Recruit CSVs not found among the attached dataset files")


def main() -> int:
    debug_input_tree()
    _extract_all_zips()
    debug_input_tree()  # again, post-extraction
    project = unpack_payload()
    sys.path.insert(0, str(project / "src"))
    data_root = prepare_data()
    print("data root:", data_root)

    from diq_model.artifacts import CheckpointStore
    from diq_model.train import run_pipeline

    results = run_pipeline(
        store=CheckpointStore(ARTIFACTS),
        data_root=data_root,
        test_start="2017-02-01",
        seed=42,
    )
    print("\n=== Stage metrics ===")
    for stage, metrics in results.items():
        print(f"{stage:>10}: {metrics}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
