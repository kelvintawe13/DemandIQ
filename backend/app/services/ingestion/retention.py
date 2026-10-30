"""Photo retention for image ingestion.

The Privacy Policy promises that a photo is deleted 30 days after upload. An
upload's parsed rows are the operator's demand history and must survive; only
the image itself is personal data, so retention clears ``source_image`` and
``source_mime`` and leaves the upload and its observations in place.

Deleting the import remains the immediate path: ``DELETE /ingest/uploads/{id}``
removes the row (and therefore the bytes) at once.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.location import IngestUpload

# Days a stored photo may be kept after upload. Kept in one place so the job and
# its tests cannot drift apart.
PHOTO_RETENTION_DAYS = 30


def photo_retention_cutoff(
    now: datetime.datetime | None = None,
    *,
    days: int = PHOTO_RETENTION_DAYS,
) -> datetime.datetime:
    """The oldest upload time still allowed to hold a photo.

    Args:
        now: Reference instant (defaults to now, UTC).
        days: Retention window in days.

    Returns:
        ``now - days`` as an aware UTC datetime. Uploads created before it have
        their photo cleared.
    """
    if now is None:
        now = datetime.datetime.now(datetime.UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.UTC)
    return now.astimezone(datetime.UTC) - datetime.timedelta(days=days)


def purge_expired_photos(
    db: Session,
    *,
    workspace_id: uuid.UUID | None = None,
    now: datetime.datetime | None = None,
    days: int = PHOTO_RETENTION_DAYS,
) -> int:
    """Clear photos older than the retention window.

    The upload row, its preview and its committed demand rows stay: only the
    stored image and its sniffed MIME type are erased, so the record of which
    import produced a demand figure survives the photo.

    Args:
        db: Session to update.
        workspace_id: Restrict to one workspace (the scheduler runs per
            workspace); ``None`` purges every workspace, which is what a
            maintenance command wants.
        now: Reference instant (defaults to now, UTC).
        days: Retention window in days.

    Returns:
        The number of uploads whose photo was cleared.
    """
    cutoff = photo_retention_cutoff(now, days=days)
    stmt = (
        update(IngestUpload)
        .where(
            IngestUpload.created_at < cutoff,
            IngestUpload.source_image.is_not(None),
        )
        .values(source_image=None, source_mime=None)
    )
    if workspace_id is not None:
        stmt = stmt.where(IngestUpload.workspace_id == workspace_id)

    result = db.execute(stmt)
    db.flush()
    return int(result.rowcount or 0)
