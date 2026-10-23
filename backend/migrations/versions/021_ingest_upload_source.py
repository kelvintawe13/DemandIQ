"""Keep the original photo with the ingest upload that read it.

Image ingestion stores its result (the parsed rows) in ``preview`` and threw the
photo away. That made the review step unverifiable after the fact: the operator
is asked to check each extracted row against the photo, but once the upload
finished the only copy of the photo was in their browser, and was gone on
reload. A misread row (``98`` becoming ``8``) then had nothing to be corrected
against.

The bytes are stored on the upload rather than in object storage: the file
channel already caps images at 5 MiB, uploads are rows the workspace owns and
deletes with the row (a CASCADE would otherwise orphan objects nothing sweeps),
and the deployment has no bucket configured. ``source_image`` is mapped as a
deferred column, so listing uploads never reads it.

``source_mime`` is the format sniffed from the bytes at upload time — the same
check that rejects a non-image — so serving the photo cannot be steered by a
client-supplied Content-Type.

Existing rows keep NULL: their photos are genuinely gone, and a guessed
placeholder would be worse than an absent one.

Revision ID: 021_ingest_upload_source
Revises: 020_explanation_attribution
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "021_ingest_upload_source"
down_revision = "020_explanation_attribution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingest_uploads", sa.Column("source_image", sa.LargeBinary(), nullable=True))
    op.add_column("ingest_uploads", sa.Column("source_mime", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ingest_uploads", "source_mime")
    op.drop_column("ingest_uploads", "source_image")
