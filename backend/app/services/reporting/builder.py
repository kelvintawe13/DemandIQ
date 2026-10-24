"""Report builder — queueing, listing and signed download links.

Per ARCHITECTURE.md §6 and API_ENDPOINTS.md reporting:

* ``POST /reports/generate`` **queues** a report and returns its id. The row is
  ``queued`` when the response is sent; ``rendering`` and ``completed`` are set
  by the ``report_generation`` worker job, which does the actual work through
  :mod:`app.services.reporting.render`. Reports used to be marked ``completed``
  inside this module with a file URI that pointed at nothing.
* The rendered bytes live on the row (``reports.content``, see migration 022)
  and are served by ``GET /reports/{id}/download``.
* ``build_signed_url`` mints an expiring HMAC link to that endpoint. The link is
  what the UI opens with ``window.open``, so it has to work without an
  ``Authorization`` header — the signature is the authorisation, and it is bound
  to one report and one expiry.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.report import Report

logger = logging.getLogger(__name__)

# Canonical template catalogue — the source of truth behind GET /reports*
REPORT_TEMPLATES: list[dict[str, str]] = [
    {
        "id": "demand_summary",
        "name": "Demand summary",
        "description": "Visitors, growth and peak days across every location.",
        "cadence": "Weekly",
        "format": "PDF · CSV",
    },
    {
        "id": "forecast_accuracy",
        "name": "Forecast accuracy",
        "description": "Predicted versus actual demand with confidence bands.",
        "cadence": "Monthly",
        "format": "PDF",
    },
    {
        "id": "waste_reduction",
        "name": "Waste reduction",
        "description": "Prep volume against served demand and estimated savings.",
        "cadence": "Monthly",
        "format": "PDF · XLSX",
    },
    {
        "id": "data_quality_audit",
        "name": "Data quality audit",
        "description": "Ingestion health, duplicates, gaps and OCR confidence.",
        "cadence": "On demand",
        "format": "CSV",
    },
]

TEMPLATE_IDS = {t["id"] for t in REPORT_TEMPLATES}
ALLOWED_FORMATS = {"pdf", "csv", "xlsx"}

#: How long a minted download link stays valid.
DOWNLOAD_URL_TTL_SECONDS = 3600

#: Domain separator, so a link signature can never be replayed as a JWT (or any
#: other HMAC this service makes with the same secret).
_SIGNING_CONTEXT = b"report-download.v1"


def object_key(report: Report) -> str:
    """Where the row says its bytes live — the identity of the stored file."""
    return f"reports/{report.id}.{report.format}"


def file_uri(report: Report) -> str:
    """A URI that names the storage, so it cannot be mistaken for an S3 key."""
    return f"db://{object_key(report)}"


def _signing_key() -> bytes:
    secret = get_settings().jwt_secret.encode()
    return hmac.new(secret, _SIGNING_CONTEXT, hashlib.sha256).digest()


def _signature(report_id: uuid.UUID, expires: int) -> str:
    message = f"{report_id}:{expires}".encode()
    return hmac.new(_signing_key(), message, hashlib.sha256).hexdigest()[:32]


def build_signed_url(report: Report, expires_seconds: int = DOWNLOAD_URL_TTL_SECONDS) -> str | None:
    """Mint an expiring download link for a completed report.

    Returns ``None`` while the report is not ready: a link that 409s is worse
    than no link, because the UI offers it as a Download button.
    """
    if report.status != "completed" or report.content is None:
        return None
    expires = int(datetime.datetime.now(datetime.UTC).timestamp()) + expires_seconds
    sig = _signature(report.id, expires)
    return f"/api/v1/reports/{report.id}/download?expires={expires}&sig={sig}"


def verify_download_signature(report_id: uuid.UUID, expires: int, sig: str) -> bool:
    """Whether ``sig`` authorises a download of ``report_id`` until ``expires``.

    The comparison is constant-time, and an expired link is refused even when the
    signature is otherwise correct — the expiry is part of the signed message, so
    a valid signature over a past expiry is still an expired link.
    """
    if expires < int(datetime.datetime.now(datetime.UTC).timestamp()):
        return False
    expected = _signature(report_id, expires)
    return hmac.compare_digest(expected, (sig or "").strip().lower())


def generate_report(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    template_id: str,
    period_start: datetime.date | None,
    period_end: datetime.date | None,
    fmt: str,
    location_ids: list[uuid.UUID] | None = None,
    generated_by: uuid.UUID | None = None,
) -> Report:
    """Queue a report row for the worker to render.

    Args:
        db: Database session.
        workspace_id: Workspace scoping.
        template_id: One of ``TEMPLATE_IDS``.
        period_start: Report period start (optional — the renderer defaults it).
        period_end: Report period end (optional — the renderer defaults it).
        fmt: One of ``pdf`` / ``csv`` / ``xlsx``.
        location_ids: Optional location filter, honouring the API body.
        generated_by: Actor user id.

    Returns:
        The persisted ``Report``, with ``status='queued'`` and no content yet.

    Raises:
        ValueError: On unknown template or format, before a row is written.
    """
    if template_id not in TEMPLATE_IDS:
        raise ValueError(f"Unknown template '{template_id}'")
    fmt_norm = fmt.lower()
    if fmt_norm not in ALLOWED_FORMATS:
        raise ValueError(f"format must be one of {sorted(ALLOWED_FORMATS)}")

    report = Report(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        template=template_id,
        period_start=period_start,
        period_end=period_end,
        format=fmt_norm,
        status="queued",
        file_uri=None,
        size_bytes=None,
        content_type=None,
        # Carried on the row because the worker is a separate pass: dropping it
        # would render a workspace-wide report for a request that asked for one
        # location, which looks like a successful answer to a different question.
        location_ids=[str(loc) for loc in location_ids] if location_ids else None,
        generated_by=generated_by,
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db.add(report)
    db.flush()
    logger.info(
        "report queued id=%s template=%s format=%s locations=%s",
        report.id,
        template_id,
        fmt_norm,
        len(location_ids) if location_ids else 0,
    )
    return report


def get_report(db: Session, report_id: uuid.UUID, workspace_id: uuid.UUID) -> Report | None:
    """Fetch a single report scoped to workspace."""
    row: Report | None = db.get(Report, report_id)
    if row is None or row.workspace_id != workspace_id:
        return None
    return row


def _encode_cursor(created_at: datetime.datetime, rid: uuid.UUID) -> str:
    raw = json.dumps({"ts": created_at.isoformat(), "id": str(rid)})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime.datetime, uuid.UUID]:
    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    data = json.loads(raw)
    return datetime.datetime.fromisoformat(data["ts"]), uuid.UUID(data["id"])


def list_reports(
    db: Session,
    workspace_id: uuid.UUID,
    *,
    cursor: str | None = None,
    limit: int = 20,
) -> tuple[list[Report], str | None, bool]:
    """Cursor-paginated report listing (newest first).

    Returns:
        Tuple ``(page, next_cursor, has_more)``.
    """
    q = select(Report).where(Report.workspace_id == workspace_id)
    if cursor:
        try:
            ts, rid = _decode_cursor(cursor)
            q = q.where(Report.created_at < ts)
            _ = rid  # tie-breaker reserved
        except Exception as exc:  # pragma: no cover
            raise ValueError("Invalid pagination cursor") from exc
    q = q.order_by(Report.created_at.desc(), Report.id.desc()).limit(limit + 1)
    rows = list(db.scalars(q).all())
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None
    return page, next_cursor, has_more


def render_stored_report(db: Session, report: Report) -> Report:
    """Render one queued report into its row.

    Split out of the job so the same call can be made from a one-off script or a
    test. Idempotent in the sense that matters: a report that already has content
    is left alone, so a retried tick cannot replace a good file with a worse one.
    """
    from app.services.reporting import render as renderer

    if report.status == "completed" and report.content is not None:
        return report

    report.status = "rendering"
    report.error = None
    db.flush()

    try:
        # Inside the try: a malformed stored id has to mark the row ``failed``
        # like any other render error, not escape and leave it ``rendering``.
        requested = [uuid.UUID(str(loc)) for loc in (report.location_ids or [])]
        document = renderer.collect_document(
            db,
            workspace_id=report.workspace_id,
            template=report.template,
            period_start=report.period_start,
            period_end=report.period_end,
            location_ids=requested or None,
        )
        content, content_type = renderer.render(document, report.format)
    except Exception as exc:
        # The row records why, in the same transaction the job is using, so the
        # failure is visible on GET /reports/{id} instead of only in a log.
        report.status = "failed"
        report.error = f"{exc.__class__.__name__}: {exc}"[:500]
        db.flush()
        raise

    report.content = content
    report.content_type = content_type
    report.size_bytes = len(content)
    report.file_uri = file_uri(report)
    report.status = "completed"
    db.flush()
    logger.info(
        "report rendered id=%s format=%s bytes=%s",
        report.id,
        report.format,
        len(content),
    )
    return report
