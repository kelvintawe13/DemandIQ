"""Sessions — rename ``sessions.ip_country`` to ``sessions.origin``.

The column was named for a country code but has only ever held
``request.client.host``: ``auth.py`` writes the client's address at sign-in
(login, MFA verify, Google OAuth) and ``/me/sessions`` hands the value straight
back. On the live database every row reads ``127.0.0.1``.

Two consequences, and this migration is about the second:

* any client that renders the field has to invent a meaning for it — the
  Devices screen said "Signed in from RW" in its tests and "Signed in from
  127.0.0.1" in production, from the same code;
* the name is what a future change trusts. The next person to touch session
  tracking reads ``ip_country`` and assumes a resolver exists somewhere that
  turns the address into an ISO code. It does not.

The rename is the whole fix: the value stays exactly as recorded, and it is
now named what it is. A deployment that resolves a country at sign-in can write
an ISO code into ``origin`` and the screens will say "Signed in from RW" —
which will be true there, and is not claimed here.

Revision ID: 013_session_origin
Revises: 012_forecast_fallback
"""

from __future__ import annotations

from alembic import op

revision = "013_session_origin"
down_revision = "012_forecast_fallback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("sessions", "ip_country", new_column_name="origin")


def downgrade() -> None:
    op.alter_column("sessions", "origin", new_column_name="ip_country")
