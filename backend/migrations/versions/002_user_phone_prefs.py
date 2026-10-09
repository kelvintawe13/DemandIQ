"""Add phone + preferences JSONB to users + normalized preference tables.

Revision ID: 002_user_phone_prefs
Revises: 001_initial
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "002_user_phone_prefs"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Extensions — ensure citext exists for email column (idempotent)
    op.execute('CREATE EXTENSION IF NOT EXISTS "citext"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    # --- users: add phone + JSONB preference mirrors (senior: keep both JSONB and normalized)
    # Use IF NOT EXISTS via DO block for idempotency across fresh vs brownfield DBs.
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='phone') THEN
                ALTER TABLE users ADD COLUMN phone TEXT;
            END IF;
        END $$;
        """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='preferences') THEN
                ALTER TABLE users ADD COLUMN preferences JSONB;
            END IF;
        END $$;
        """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='notification_settings') THEN
                ALTER TABLE users ADD COLUMN notification_settings JSONB;
            END IF;
        END $$;
        """)

    # --- normalized tables (mirror of users.preferences) — create if not exists
    # user_preferences
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
          user_id             UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          theme               TEXT NOT NULL DEFAULT 'light',
          language            TEXT NOT NULL DEFAULT 'en',
          density             TEXT NOT NULL DEFAULT 'comfortable',
          default_location_id UUID REFERENCES locations(id) ON DELETE SET NULL,
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """)
    # user_notification_settings
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_notification_settings (
          user_id       UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          email_digest  BOOLEAN NOT NULL DEFAULT true,
          push_alerts   BOOLEAN NOT NULL DEFAULT true,
          in_app_alerts BOOLEAN NOT NULL DEFAULT true,
          weekly_report BOOLEAN NOT NULL DEFAULT false,
          updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_notification_settings")
    op.execute("DROP TABLE IF EXISTS user_preferences")
    # Columns kept for downgrade safety — only drop if you intend full revert
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS notification_settings")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS preferences")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS phone")
