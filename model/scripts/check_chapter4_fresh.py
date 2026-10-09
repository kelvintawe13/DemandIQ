#!/usr/bin/env python3
"""Fail if the committed Chapter 4 draft (or its figures) has drifted.

The draft and its figures are *generated* artifacts: ``make chapter4`` renders
them from a training run. If the renderer or the pipeline changes and someone
forgets to regenerate, the committed copy silently disagrees with the code -
exactly the "every number traces to a stage" rule the renderer exists to
enforce.

This check regenerates the draft into a temporary directory and compares it,
character for character, against the committed file, then verifies every figure
the draft references exists and is non-empty.

``model/artifacts/`` is gitignored, so a fresh checkout has no run to regenerate
from. In that case the check SKIPS with exit 0 rather than failing a build that
could not have produced the draft. Point ``--artifacts`` at any run (e.g.
``make train``) to make it real.

Usage:
    python scripts/check_chapter4_fresh.py
    python scripts/check_chapter4_fresh.py --artifacts artifacts/runs/kaggle
    python scripts/check_chapter4_fresh.py --strict-figures
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

FIGURE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
DIFF_LIMIT = 6000


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fail(message: str) -> int:
    print(f"FAIL: {message}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--artifacts", default="artifacts/runs/latest")
    ap.add_argument("--draft", default="CHAPTER4_DRAFT.md")
    ap.add_argument(
        "--strict-figures",
        action="store_true",
        help="also byte-compare figures (PNG bytes can differ across matplotlib builds)",
    )
    args = ap.parse_args()

    root = Path(args.artifacts)
    draft = Path(args.draft)
    renderer = Path("scripts/render_chapter4.py")

    if not (root / "manifest.json").exists():
        print(
            f"SKIP: no run artifacts at '{root}' (model/artifacts is gitignored). "
            "Regenerate a run with `make train` to enable this check."
        )
        return 0
    if not draft.exists():
        return _fail(f"committed draft '{draft}' does not exist")
    if not renderer.exists():
        return _fail(f"renderer '{renderer}' not found (run this from model/)")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        tmp_draft = tmp / "CHAPTER4_DRAFT.md"
        proc = subprocess.run(
            [
                sys.executable,
                str(renderer),
                "--artifacts",
                str(root),
                "--out",
                str(tmp_draft),
                "--figures",
                str(tmp / "figures"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr)
            return _fail("the renderer failed to regenerate the draft")

        regenerated = tmp_draft.read_text()
        committed = draft.read_text()
        if regenerated != committed:
            diff = difflib.unified_diff(
                committed.splitlines(keepends=True),
                regenerated.splitlines(keepends=True),
                fromfile=f"{draft} (committed)",
                tofile="regenerated from artifacts",
                n=2,
            )
            print("".join(diff)[:DIFF_LIMIT])
            return _fail(f"'{draft}' is stale - run `make chapter4` and commit the result")

        links = FIGURE_RE.findall(committed)
        if not links:
            print("NOTE: the draft references no figures (did the renderer skip them?)")

        missing: list[str] = []
        empty: list[str] = []
        for link in links:
            target = draft.parent / link
            if not target.exists():
                missing.append(link)
            elif target.stat().st_size == 0:
                empty.append(link)
        if missing or empty:
            return _fail(f"figures missing={missing} empty={empty}")

        if args.strict_figures:
            drifted = [
                link
                for link in links
                if not (tmp / link).exists() or _sha256(draft.parent / link) != _sha256(tmp / link)
            ]
            if drifted:
                return _fail(f"figure bytes differ from a fresh render: {drifted}")

    print(f"OK: '{draft}' and its {len(links)} figure(s) match the artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
