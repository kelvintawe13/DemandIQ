#!/usr/bin/env python3
"""Remove model_versions rows stuck in ``status='training'`` (orphan cleanup).

``POST /api/v1/models/train`` and the ``full_retrain`` job both queue a run by
inserting a ``model_versions`` row with ``status='training'`` and a companion
calibration stub — a placeholder for work a trainer would pick up. Nothing in
this deployment ever moves such a row out of ``training`` (there is no fitted
artifact to attach), so a queued run that is never executed leaves a permanent
orphan: it appears in the registry as a model that has been "training" since it
was created, with no MAE, no artifact and no effect on serving.

This deletes only true orphans:

* ``status = 'training'``,
* ``artifact_uri IS NULL`` and ``mae IS NULL`` (nothing was ever produced), and
* not referenced by any ``forecasts`` row (a real forecast still needs its
  ``model_version_id``; ``forecasts.model_version_id`` has no ``ON DELETE``
  action, so those rows are skipped rather than forced).

Its companion ``calibrations`` stub is removed by the ``ON DELETE CASCADE``
foreign key. Anything else — a completed candidate, the production row, the
``v0-fallback`` row — is left untouched.

Usage:
    backend/.venv/bin/python backend/scripts/cleanup_stuck_training.py --dry-run
    backend/.venv/bin/python backend/scripts/cleanup_stuck_training.py
    backend/.venv/bin/python backend/scripts/cleanup_stuck_training.py --workspace "Test Co"
"""

from __future__ import annotations

import argparse
import os
import sys

import sqlalchemy as sa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings  # noqa: E402

_SELECT_ORPHANS = sa.text("""
    SELECT mv.id,
           w.name AS workspace,
           mv.algorithm,
           mv.version,
           mv.trained_at,
           EXISTS (SELECT 1 FROM forecasts f WHERE f.model_version_id = mv.id) AS referenced
    FROM model_versions mv
    JOIN workspaces w ON w.id = mv.workspace_id
    WHERE mv.status = 'training'
      AND mv.artifact_uri IS NULL
      AND mv.mae IS NULL
      AND (:workspace IS NULL OR w.name = :workspace OR w.id::text = :workspace)
    ORDER BY w.name, mv.algorithm, mv.version
    """)


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete orphan 'training' model versions.")
    parser.add_argument(
        "--dry-run", action="store_true", help="List what would be deleted without deleting"
    )
    parser.add_argument(
        "--workspace",
        default=os.environ.get("DEMO_WORKSPACE"),
        help="Restrict to one workspace (name or id); default: all workspaces",
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = sa.create_engine(settings.database_url)
    with engine.begin() as conn:
        rows = conn.execute(_SELECT_ORPHANS, {"workspace": args.workspace}).all()
        candidates = [r for r in rows if not r.referenced]
        kept = [r for r in rows if r.referenced]

        if not candidates and not kept:
            print("No orphan 'training' rows found — nothing to do.")
            return

        for r in candidates:
            print(
                f"  {'would delete' if args.dry_run else 'deleted'}: "
                f"{r.workspace:<16} {r.algorithm:<10} {r.version:<12} "
                f"(queued {r.trained_at})"
            )
        for r in kept:
            print(f"  kept (referenced by a forecast): {r.workspace} {r.algorithm} {r.version}")

        if args.dry_run:
            print(f"Dry run: {len(candidates)} orphan row(s) would be deleted.")
            return

        deleted = 0
        for r in candidates:
            conn.execute(sa.text("DELETE FROM model_versions WHERE id = :id"), {"id": r.id})
            deleted += 1
        print(f"Deleted {deleted} orphan 'training' row(s); {len(kept)} kept.")


if __name__ == "__main__":
    main()
