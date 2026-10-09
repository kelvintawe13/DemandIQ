#!/usr/bin/env python3
"""Seed one demo account per role (idempotent).

The workspace previously had owners only, so the administrator, data clerk,
analyst and viewer experiences could not be reached or tested. This creates one
signable account per role, matching the capability bundles in
``app/security/permissions.py`` and the demo seats in
``frontend/src/data/demoAccount.ts``:

  demo@demandiq.app     / DemandIQ2026!   owner          business + governance
  admin@demandiq.app    / Admin@2026!     administrator  models + platform
  clerk@demandiq.app    / Clerk@2026!     operator       data in, data clean
  analyst@demandiq.app  / Analyst@2026!   analyst        analysis + simulation
  viewer@demandiq.app   / Viewer@2026!    viewer         read-only

Re-running refreshes the password/role and reactivates the account; it never
deletes anything. ``admin@demandiq.app`` keeps its platform flag, so the Site
Admin console stays reachable from the administrator seat.

**Which workspace.** Every seat is put in the same workspace, and that workspace
must be the one that owns the demo data — locations, forecasts, alerts. It is
resolved as the workspace with the most demo rows (a workspace with 0 locations
and 0 forecasts demos nothing), and may be pinned explicitly:

    backend/.venv/bin/python backend/scripts/seed_demo_roles.py --workspace "DemandIQ Demo"

Usage:
    backend/.venv/bin/python backend/scripts/seed_demo_roles.py [--workspace NAME_OR_ID]
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid

import sqlalchemy as sa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings
from app.security.hashing import hash_password

# (email, password, name, role, job title, is_site_admin)
DEMO_ACCOUNTS: list[tuple[str, str, str, str, str, bool]] = [
    ("demo@demandiq.app", "DemandIQ2026!", "Demo Owner", "owner", "Owner", False),
    (
        "admin@demandiq.app",
        "Admin@2026!",
        "Site Admin",
        "administrator",
        "Platform Administrator",
        True,
    ),
    ("clerk@demandiq.app", "Clerk@2026!", "Aline Umutoni", "operator", "Data Clerk", False),
    ("analyst@demandiq.app", "Analyst@2026!", "Yves Habimana", "analyst", "Demand Analyst", False),
    ("viewer@demandiq.app", "Viewer@2026!", "Grace Uwase", "viewer", "Auditor", False),
]

# Demo rows that make a workspace worth demoing in. The seat goes where the
# data is: a workspace with no locations and no forecasts shows empty screens.
DEMO_SIGNAL_TABLES = ("locations", "forecasts")


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def resolve_workspace(
    conn: sa.Connection, requested: str | None
) -> tuple[str, str, dict[str, int]]:
    """Return ``(id, name, signal_counts)`` for the workspace to seat seats in."""

    rows = conn.execute(
        sa.text("SELECT id, name, created_at FROM workspaces ORDER BY created_at ASC")
    ).all()
    if not rows:
        return "", "", {}

    def signal(ws_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in DEMO_SIGNAL_TABLES:
            # Built with SQLAlchemy Core rather than an f-string, so the table
            # name is never interpolated into SQL text by hand. The name still
            # comes from DEMO_SIGNAL_TABLES, never from input.
            stmt = (
                sa.select(sa.func.count())
                .select_from(sa.table(table))
                .where(sa.column("workspace_id") == sa.bindparam("wid"))
            )
            counts[table] = int(conn.execute(stmt, {"wid": ws_id}).scalar() or 0)
        return counts

    if requested:
        match = next(
            (r for r in rows if str(r[0]) == requested or str(r[1]).lower() == requested.lower()),
            None,
        )
        if match is None:
            names = ", ".join(f"{r[1]} ({r[0]})" for r in rows)
            raise SystemExit(f"No workspace matches {requested!r}. Known workspaces: {names}")
        return str(match[0]), str(match[1]), signal(str(match[0]))

    # Most demo rows wins; ties and empty workspaces fall back to the oldest.
    scored = [(sum(signal(str(r[0])).values()), -index, r) for index, r in enumerate(rows)]
    _, _, best = max(scored, key=lambda item: (item[0], item[1]))
    return str(best[0]), str(best[1]), signal(str(best[0]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed one demo account per role.")
    parser.add_argument(
        "--workspace",
        default=os.environ.get("DEMO_WORKSPACE"),
        help="Workspace name or id to seat the demo accounts in (default: the one owning the demo data)",
    )
    args = parser.parse_args()

    settings = get_settings()
    engine = sa.create_engine(settings.database_url)
    with engine.begin() as conn:
        ws_id, ws_name, counts = resolve_workspace(conn, args.workspace)
        if not ws_id:
            conn.execute(
                sa.text(
                    "INSERT INTO workspaces (id, name, business_type, country, timezone, created_at) "
                    "VALUES (gen_random_uuid(), 'DemandIQ HQ', 'cafe', 'RW', 'Africa/Kigali', now())"
                )
            )
            ws_id, ws_name = conn.execute(
                sa.text("SELECT id, name FROM workspaces ORDER BY created_at ASC LIMIT 1")
            ).one()
            counts = {}

        signal_text = ", ".join(f"{v} {k}" for k, v in counts.items()) or "no demo rows yet"
        print(f"Workspace: {ws_name} ({ws_id}) — {signal_text}")
        if counts and sum(counts.values()) == 0:
            print("  ! this workspace holds no demo data; pass --workspace to pick another")

        print("Seating demo accounts:")
        moved: list[str] = []
        for email, password, name, role, job_title, site_admin in DEMO_ACCOUNTS:
            existing = conn.execute(
                sa.text("SELECT id, workspace_id FROM users WHERE email = :e"), {"e": email}
            ).first()
            params = {
                "wid": ws_id,
                "name": name,
                "email": email,
                "ph": hash_password(password),
                "role": role,
                "site_admin": site_admin,
                "job_title": job_title,
            }
            if existing is None:
                conn.execute(
                    sa.text(
                        "INSERT INTO users (id, workspace_id, name, email, password_hash, role, "
                        "email_verified, profile_completed, is_site_admin, job_title, created_at) "
                        "VALUES (gen_random_uuid(), :wid, :name, :email, :ph, CAST(:role AS user_role), "
                        "true, true, :site_admin, :job_title, now())"
                    ),
                    params,
                )
                print(f"  created   {email:<22} {role}")
                continue

            # Only ever move a seat when it is currently seated elsewhere: the
            # point is that every seat shares the workspace the data lives in.
            if str(existing[1]) != ws_id:
                was = conn.execute(
                    sa.text("SELECT name FROM workspaces WHERE id = :wid"), {"wid": existing[1]}
                ).scalar()
                moved.append(f"{email} (was in {was or existing[1]})")
            conn.execute(
                sa.text(
                    "UPDATE users SET workspace_id = :wid, name = :name, password_hash = :ph, "
                    "role = CAST(:role AS user_role), email_verified = true, profile_completed = true, "
                    "deactivated_at = NULL, is_site_admin = :site_admin, job_title = :job_title "
                    "WHERE email = :e"
                ),
                {**params, "e": email},
            )
            print(f"  refreshed {email:<22} {role}")

        if moved:
            print("  moved into this workspace (their data lives here now):")
            for line in moved:
                print(f"    - {line}")

        rows = conn.execute(
            sa.text(
                "SELECT role::text, count(*) FROM users WHERE email = ANY(:emails) GROUP BY 1 ORDER BY 1"
            ),
            {"emails": [a[0] for a in DEMO_ACCOUNTS]},
        ).all()
        print("Roles now present for the demo seats:")
        for role, count in rows:
            print(f"  {role:<15} {count}")
        print(f"All {len(DEMO_ACCOUNTS)} seats are in {ws_name}.")
    print("DONE")


if __name__ == "__main__":
    main()
