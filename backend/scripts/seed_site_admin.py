#!/usr/bin/env python3
"""Seed the site-admin account + default platform settings (idempotent).

Creates/updates:
  admin@demandiq.app / Admin@2026!      — platform administrator (the "Fill admin" account)
  demo@demandiq.app / DemandIQ2026!     — demo workspace owner (kept in sync)

Roles must agree with scripts/seed_demo_roles.py, which seats one account per
role: the platform console belongs to the administrator, while the business
(ownership) side belongs to the workspace owner.

Also seeds default platform_settings rows so the Site Admin console has
real values to edit from day one.

Usage: backend/.venv/bin/python backend/scripts/seed_site_admin.py

Override the demo credentials with DEMANDIQ_ADMIN_EMAIL / DEMANDIQ_ADMIN_PASSWORD /
DEMANDIQ_DEMO_EMAIL / DEMANDIQ_DEMO_PASSWORD when seeding anything that is not a
throwaway demo database.
"""

from __future__ import annotations

import os
import sys

import sqlalchemy as sa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings
from app.security.hashing import hash_password

# The defaults below are the published demo credentials (they are printed in the
# usage block above so a demo database can be signed into). They are overridable
# by environment so that seeding anything that is *not* a throwaway demo does not
# silently install a password that is in the repository.
ADMIN_EMAIL = os.environ.get("DEMANDIQ_ADMIN_EMAIL", "admin@demandiq.app")
ADMIN_PASSWORD = os.environ.get("DEMANDIQ_ADMIN_PASSWORD", "Admin@2026!")
ADMIN_NAME = "Site Admin"
ADMIN_JOB_TITLE = "Platform Administrator"
DEMO_EMAIL = os.environ.get("DEMANDIQ_DEMO_EMAIL", "demo@demandiq.app")
DEMO_PASSWORD = os.environ.get("DEMANDIQ_DEMO_PASSWORD", "DemandIQ2026!")

DEFAULT_SETTINGS = {
    "registration_open": True,
    "default_role": "viewer",
    "announcement": "",
    "support_email": "support@demandiq.app",
    "maintenance_mode": False,
}


def main() -> None:
    settings = get_settings()
    engine = sa.create_engine(settings.database_url)
    with engine.begin() as conn:
        ws_row = conn.execute(
            sa.text("SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1")
        ).first()
        if ws_row is None:
            conn.execute(
                sa.text(
                    "INSERT INTO workspaces (id, name, business_type, country, timezone, created_at) "
                    "VALUES (gen_random_uuid(), 'DemandIQ HQ', 'cafe', 'RW', 'Africa/Kigali', now())"
                )
            )
            ws_row = conn.execute(
                sa.text("SELECT id FROM workspaces ORDER BY created_at ASC LIMIT 1")
            ).first()
        if ws_row is None:
            raise SystemExit("no workspace found and none could be created")
        ws_id = str(ws_row[0])

        def upsert_user(
            email: str,
            password: str,
            name: str,
            role: str,
            job_title: str,
            site_admin: bool = False,
        ) -> None:
            existing = conn.execute(
                sa.text("SELECT id FROM users WHERE email = :e"), {"e": email}
            ).first()
            pw_hash = hash_password(password)
            if existing is None:
                conn.execute(
                    sa.text(
                        "INSERT INTO users (id, workspace_id, name, email, password_hash, role, "
                        "email_verified, profile_completed, is_site_admin, created_at) "
                        "VALUES (gen_random_uuid(), :wid, :name, :email, :ph, CAST(:role AS user_role), "
                        "true, true, :site_admin, now())"
                    ),
                    {
                        "wid": ws_id,
                        "name": name,
                        "email": email,
                        "ph": pw_hash,
                        "role": role,
                        "site_admin": site_admin,
                    },
                )
                print(f"  created {email} ({role}{', site-admin' if site_admin else ''})")
            else:
                conn.execute(
                    sa.text(
                        "UPDATE users SET password_hash = :ph, role = CAST(:role AS user_role), "
                        "email_verified = true, profile_completed = true, deactivated_at = NULL, "
                        "is_site_admin = :site_admin WHERE email = :e"
                    ),
                    {"ph": pw_hash, "role": role, "e": email, "site_admin": site_admin},
                )
                print(f"  refreshed {email} ({role}{', site-admin' if site_admin else ''})")

        print("Seating admin accounts:")
        upsert_user(
            ADMIN_EMAIL,
            ADMIN_PASSWORD,
            ADMIN_NAME,
            "administrator",
            ADMIN_JOB_TITLE,
            site_admin=True,
        )
        upsert_user(
            DEMO_EMAIL, DEMO_PASSWORD, "Demo Owner", "owner", "Demo account", site_admin=False
        )

        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                sa.text(
                    "INSERT INTO platform_settings (key, value, updated_at) "
                    "VALUES (:k, CAST(:v AS jsonb), now()) "
                    "ON CONFLICT (key) DO NOTHING"
                ),
                {"k": key, "v": __import__("json").dumps(value)},
            )
        print(f"  ensured {len(DEFAULT_SETTINGS)} platform settings")
    print("DONE")


if __name__ == "__main__":
    main()
