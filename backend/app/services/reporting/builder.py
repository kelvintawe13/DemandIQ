"""Report builder — async generation stubbed to object store (S3).

Per ARCHITECTURE.md §6 and API_ENDPOINTS.md reporting:

* Generation is asynchronous into the object store; the API returns a
  ``report_id`` and the UI polls ``GET /reports/{id}`` for a signed URL.
* This module stubs the object store: files are not uploaded to real S3,
  but a deterministic ``file_uri`` is persisted and a time-limited signed
  URL is synthesized on read.
* Generation is simulated synchronously for testability (status becomes
  ``completed`` immediately). In production the queued row would be picked
  up by a worker; the polling contract is preserved regardless.
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

# In-memory queue stub (production uses Redis/RQ)
_queued: dict[str, uuid.UUID] = {}


def _object_key(report_id: uuid.UUID, fmt: str) -> str:
    return f"reports/{report_id}.{fmt.lower()}"


def _stub_file_uri(report_id: uuid.UUID, fmt: str) -> str:
    cfg = get_settings()
    base = cfg.object_store_url or "s3://demandiq-reports"
    return f"{base.rstrip('/')}/{_object_key(report_id, fmt)}"


def build_signed_url(report: Report, expires_seconds: int = 3600) -> str | None:
    """Synthesize a signed URL for a completed report.

    In production this would be a presigned S3 URL. Here we create an
    HMAC-signed URL so that polling tests can verify a ``signed_url``
    without needing real S3 credentials.

    Args:
        report: Completed ``Report`` row with a ``file_uri``.
        expires_seconds: URL lifetime.

    Returns:
        Signed URL string, or ``None`` when the report is not yet ready.
    """
    if report.status != "completed" or report.file_uri is None:
        return None
    cfg = get_settings()
    secret = (cfg.object_store_secret or cfg.jwt_secret).encode()
    expires = int(datetime.datetime.now(datetime.UTC).timestamp()) + expires_seconds
    payload = f"{report.id}:{report.file_uri}:{expires}".encode()
    sig = hmac.new(secret, payload, hashlib.sha256).hexdigest()[:16]
    # Use file_uri as path; synthesize an https URL for the UI.
    # ``object_store_url`` may be s3://..., convert to https stub.
    base = "https://object-store.demandiq.local"
    if cfg.object_store_url and cfg.object_store_url.startswith("https://"):
        base = cfg.object_store_url.rstrip("/")
    return f"{base}/{_object_key(report.id, report.format)}?expires={expires}&sig={sig}"


def _encode_cursor(created_at: datetime.datetime, rid: uuid.UUID) -> str:
    raw = json.dumps({"ts": created_at.isoformat(), "id": str(rid)})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime.datetime, uuid.UUID]:
    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    data = json.loads(raw)
    return datetime.datetime.fromisoformat(data["ts"]), uuid.UUID(data["id"])


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
    """Create a report row, stub async generation and mark completed.

    Args:
        db: Database session.
        workspace_id: Workspace scoping.
        template_id: One of ``TEMPLATE_IDS``.
        period_start: Report period start (optional).
        period_end: Report period end (optional).
        fmt: One of ``pdf`` / ``csv`` / ``xlsx``.
        location_ids: Optional location filter (stored in audit, not schema).
        generated_by: Actor user id.

    Returns:
        Persisted ``Report`` with ``status='completed'`` and ``file_uri`` set.

    Raises:
        ValueError: On unknown template or format.
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
        generated_by=generated_by,
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db.add(report)
    db.flush()

    # --- Simulate async worker (synchronous completion) --------------------
    # In production a worker would render the file and upload to S3.
    # Here we complete immediately so that polling tests see a ready report.
    report.file_uri = _stub_file_uri(report.id, fmt_norm)
    # Rough size per format
    size_map = {"pdf": 1_200_000, "csv": 220_000, "xlsx": 740_000}
    report.size_bytes = size_map.get(fmt_norm, 500_000)
    report.status = "completed"
    db.flush()

    # Track queued alias for introspection (not used in tests)
    _queued[str(report.id)] = report.id
    logger.info(
        "report generated id=%s workspace=%s template=%s format=%s by=%s",
        report.id,
        workspace_id,
        template_id,
        fmt_norm,
        generated_by,
    )
    _ = location_ids  # reserved for future filtering
    return report


def get_report(db: Session, report_id: uuid.UUID, workspace_id: uuid.UUID) -> Report | None:
    """Fetch a single report scoped to workspace."""
    row: Report | None = db.get(Report, report_id)
    if row is None or row.workspace_id != workspace_id:
        return None
    return row


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
