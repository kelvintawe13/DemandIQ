"""Ingestion router — manual, file and image channels.

All routes are workspace-scoped and require authentication. Every committed row
goes through :func:`commit_demand` so that idempotency, future-date rejection
and source tagging are enforced in one place.
"""

from __future__ import annotations

import datetime
import logging
import re
import uuid
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.location import DemandObservation, IngestUpload, Location
from app.models.location_access import LocationAccessLog
from app.models.user import User
from app.security.permissions import INGEST_WRITE, has_capability
from app.services.ingestion.file import (
    apply_column_map,
    parse_file_table,
    suggest_column_map,
)
from app.services.ingestion.image import (
    ImageValidationError,
    image_mime_type,
    preview_for_image,
    validate_image_upload,
)
from app.services.ingestion.location_match import match_location_id
from app.services.ingestion.normaliser import (
    NormaliserError,
    commit_demand,
    validate_demand_qty,
)

logger = logging.getLogger(__name__)


def require_ingest_write(user: Annotated[User, Depends(get_current_user)]) -> User:
    """Every ingestion route writes demand data, so the gate sits on the router."""
    if not has_capability(user, INGEST_WRITE):
        raise _error(403, "forbidden", "Data ingest permission required")
    return user


router = APIRouter(
    prefix="/api/v1/ingest",
    tags=["ingestion"],
    dependencies=[Depends(require_ingest_write)],
)

MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MiB for CSV/XLSX (frontend ExcelCsvTab 50 MB)
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MiB for handwritten images


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# `[source:file]` is what the normaliser writes; a bare `source:file` is what it
# wrote before the two sides agreed, and existing rows still say that.
_SOURCE_TAG = re.compile(r"\[source:([A-Za-z0-9_]+)\]|\bsource:([A-Za-z0-9_]+)")


def _source_tag(notes: str | None) -> str | None:
    """The channel recorded in a row's notes, in either tag format."""
    if not notes:
        return None
    match = _SOURCE_TAG.search(notes)
    if match is None:
        return None
    return match.group(1) or match.group(2)


def _has_source_expr() -> Any:
    """SQL-side "this upload kept its photo", without reading the column.

    ``source_image`` is deferred precisely so a page of uploads does not pull
    megabytes into the session. Testing it in Python would fetch every photo, so
    the question is asked in SQL instead and only the boolean comes back.
    """
    return IngestUpload.source_image.is_not(None).label("has_source")


def _download_name(filename: str | None) -> str:
    """An ASCII-safe filename for ``Content-Disposition``.

    The stored name came from the browser and may contain quotes, newlines or
    non-ASCII characters that would either break the header or inject into it.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", filename or "upload")
    return cleaned.strip("._") or "upload"


def _error(code: int, err: str, msg: str, field: str | None = None) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"error": {"code": err, "message": msg, "field": field, "reference": None}},
    )


def _client_from_request(request: Request) -> str:
    header = request.headers.get("x-client")
    if header in ("web", "mobile", "api"):
        return header
    ua = (request.headers.get("user-agent") or "").lower()
    if "mobile" in ua or "android" in ua or "iphone" in ua:
        return "mobile"
    return "web"


def _log_access(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user: User | None,
    location_id: uuid.UUID | None,
    action: str,
    request: Request,
) -> None:
    try:
        entry = LocationAccessLog(
            workspace_id=workspace_id,
            location_id=location_id,
            user_id=user.id if user else None,
            action=action,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            client=_client_from_request(request),
        )
        db.add(entry)
        db.flush()
    except Exception as exc:  # pragma: no cover
        logger.warning("Failed to write location_access_log (ingest): %s", exc)


def _location_candidates(db: Session, workspace_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    """Every active location a fuzzy match may choose from, read once.

    Loaded once per mapping/commit instead of once per row: a 1000-row file
    would otherwise scan the workspace's locations 1000 times, and the fuzzy
    matcher needs the whole list anyway.
    """
    rows = db.scalars(
        select(Location).where(
            Location.workspace_id == workspace_id,
            Location.archived_at.is_(None),
        )
    )
    return [(row.id, row.name) for row in rows]


def _resolve_location_id(
    db: Session,
    workspace_id: uuid.UUID,
    raw: Any,
    *,
    candidates: list[tuple[uuid.UUID, str]] | None = None,
) -> uuid.UUID | None:
    """Resolve a location reference (UUID string or name) to a UUID.

    A name the workspace does not hold exactly is matched fuzzily (see
    :mod:`app.services.ingestion.location_match`): a logbook writes "remera
    grills" for "Remer Grills", and rejecting the row over a typo is what made
    a photographed page import nothing at all.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    text = str(raw).strip()
    # Try UUID first.
    try:
        cand = uuid.UUID(text)
        loc = db.get(Location, cand)
        if loc is not None and loc.workspace_id == workspace_id and loc.archived_at is None:
            return cand
        return None
    except ValueError:
        pass
    # Cheap exact lookup before loading every location.
    loc = db.scalar(
        select(Location).where(
            Location.workspace_id == workspace_id,
            Location.name == text,
            Location.archived_at.is_(None),
        )
    )
    if loc is not None:
        return loc.id  # type: ignore[return-value]

    pool = candidates if candidates is not None else _location_candidates(db, workspace_id)
    matched = match_location_id(text, pool)
    if matched is not None:
        logger.info("fuzzy location match %r -> %s", text, matched)
    else:
        logger.info("no location match for %r (%s candidate(s))", text, len(pool))
    return matched


# ---------------------------------------------------------------------------
# Schemas (ingest-specific, kept local to avoid cross-import churn)
# ---------------------------------------------------------------------------


class ManualRow(BaseModel):
    """Single manual demand row."""

    location_id: uuid.UUID
    date: datetime.date
    demand_qty: int = Field(ge=0)
    notes: str | None = Field(default=None, max_length=500)


class ColumnMap(BaseModel):
    """Column mapping for file uploads."""

    date: str = Field(description="Header name for the date column")
    demand_qty: str = Field(description="Header name for the demand_qty column")
    location: str | None = Field(default=None, description="Header for location column")
    notes: str | None = Field(default=None)


class MappingRequest(BaseModel):
    """Body for POST /ingest/file/{upload_id}/mapping."""

    column_map: ColumnMap
    default_location_id: uuid.UUID | None = Field(default=None)


class ImageCommitRow(BaseModel):
    """Corrected OCR row."""

    location_id: uuid.UUID
    date: datetime.date
    demand_qty: int = Field(ge=0)
    notes: str | None = None


class ImageCommitRequest(BaseModel):
    """Body for POST /ingest/image/{job_id}/commit."""

    rows: list[ImageCommitRow]


# ---------------------------------------------------------------------------
# Manual
# ---------------------------------------------------------------------------


@router.post("/manual", status_code=status.HTTP_201_CREATED)
def ingest_manual(
    payload: list[ManualRow],
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Validate and upsert an array of manual demand rows."""
    if not payload:
        raise _error(400, "validation_error", "Body must be a non-empty array", field="body")

    committed = 0
    reconciled = 0
    errors: list[dict[str, Any]] = []

    for idx, row in enumerate(payload):
        try:
            result = commit_demand(
                db,
                workspace_id=current_user.workspace_id,
                location_id=row.location_id,
                date=row.date,
                demand_qty=row.demand_qty,
                source="manual",
                notes=row.notes,
            )
            if result.created:
                committed += 1
            else:
                reconciled += 1
            _log_access(
                db,
                workspace_id=current_user.workspace_id,
                user=current_user,
                location_id=row.location_id,
                action="view",
                request=request,
            )
        except NormaliserError as exc:
            errors.append(
                {"row_index": idx, "field": exc.field, "code": exc.code, "message": exc.message}
            )
        except Exception as exc:  # pragma: no cover
            errors.append(
                {"row_index": idx, "field": None, "code": "internal_error", "message": str(exc)}
            )

    if errors:
        # If any row failed, return 422 with per-row errors (partial commit still persisted).
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "validation_error",
                    "message": f"{len(errors)} row(s) failed validation",
                    "field": None,
                    "reference": getattr(request.state, "request_id", None),
                    "details": errors,
                }
            },
        )

    return {"committed": committed, "reconciled": reconciled, "total": len(payload)}


# ---------------------------------------------------------------------------
# File: upload -> mapping (dry-run) -> commit
# ---------------------------------------------------------------------------


@router.post("/file", status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    file: Annotated[UploadFile, File(description="CSV or XLSX file")],
) -> dict[str, Any]:
    """Stage a CSV/XLSX file and return headers + 5-row preview."""
    if not file.filename:
        raise _error(400, "validation_error", "Filename is required", field="file")

    lower = file.filename.lower()
    if not (
        lower.endswith(".csv")
        or lower.endswith(".xlsx")
        or lower.endswith(".xls")
        or lower.endswith(".txt")
    ):
        raise _error(400, "invalid_file", "Only CSV and XLSX files are accepted", field="file")

    data = await file.read()
    if len(data) > MAX_FILE_BYTES:
        raise _error(400, "file_too_large", f"File exceeds {MAX_FILE_BYTES} bytes", field="file")
    if not data:
        raise _error(400, "empty_file", "Uploaded file is empty", field="file")

    try:
        table = parse_file_table(file.filename, data)
    except Exception as exc:
        raise _error(400, "parse_error", f"Failed to parse file: {exc}", field="file") from exc

    headers, rows = table.headers, table.rows
    # What "Date" / "Visitors (adults)" / "No. of guests" most likely map to.
    # Sent with the preview so the mapping dropdowns arrive filled in for a file
    # that names its columns the way people name them, instead of making the
    # operator match four selects by hand.
    suggested = suggest_column_map(headers)
    preview_rows = rows[:5]
    # Make preview JSON-serialisable (dates -> iso strings where needed).
    serialisable_preview = []
    for r in preview_rows:
        serialisable_preview.append(
            {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()}
        )

    upload = IngestUpload(
        id=uuid.uuid4(),
        workspace_id=current_user.workspace_id,
        uploaded_by=current_user.id,
        kind="xlsx" if lower.endswith((".xlsx", ".xls")) else "csv",
        filename=file.filename,
        status="awaiting_mapping",
        row_count=len(rows),
        error_count=0,
        preview={
            "headers": headers,
            "rows": serialisable_preview,
            "row_count": len(rows),
            "header_row": table.header_row,
            "preamble_rows": table.preamble_rows,
            "suggested_map": suggested,
            "all_rows": [
                {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}
                for row in rows
            ],
        },
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db.add(upload)
    db.flush()

    logger.info(
        "file staged upload=%s workspace=%s rows=%s",
        upload.id,
        current_user.workspace_id,
        len(rows),
    )

    return {
        "upload_id": str(upload.id),
        "filename": file.filename,
        "detected_headers": headers,
        "preview": serialisable_preview,
        "row_count": len(rows),
        # 0 for a clean export; >0 when the table started below a title or a
        # date range, which the screen says out loud so the operator can see the
        # file was read past its preamble rather than mangled.
        "header_row": table.header_row,
        "preamble_rows": table.preamble_rows,
        "suggested_map": suggested,
        "status": upload.status,
    }


@router.post("/file/{upload_id}/mapping")
def file_mapping(
    upload_id: uuid.UUID,
    payload: MappingRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Validate column mapping and return a dry-run row count plus row errors."""
    upload: IngestUpload | None = db.get(IngestUpload, upload_id)
    if upload is None or upload.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Upload not found")

    if upload.status == "committed":
        raise _error(409, "already_committed", "Upload already committed")

    # ``upload.preview`` is a plain JSONB column: SQLAlchemy compares by object
    # identity, so mutating the loaded dict in place is *not* detected as a
    # change. Copy it and reassign at the end so the mapping actually survives
    # to the commit step (otherwise commit raises 422 ``missing_mapping``).
    preview = dict(upload.preview or {})
    headers: list[str] = preview.get("headers", [])  # type: ignore[assignment]
    all_rows: list[dict[str, Any]] = preview.get("all_rows", [])  # type: ignore[assignment]

    # Validate mapping headers exist.
    for field_name, header in payload.column_map.model_dump().items():
        if header is None:
            continue
        if header not in headers:
            raise _error(
                422,
                "invalid_mapping",
                f"Column '{header}' not found in file headers",
                field=f"column_map.{field_name}",
            )

    column_map = {k: v for k, v in payload.column_map.model_dump().items() if v is not None}

    mapped, errors, skipped = apply_column_map(
        all_rows,
        column_map,  # type: ignore[arg-type]
        default_location_id=payload.default_location_id,
    )

    # Validate location resolution for the dry-run.
    dry_errors: list[dict[str, Any]] = list(errors)
    # Read the workspace's locations once; a fuzzy name match needs the whole
    # list, and doing it per row would scan it once per row.
    location_pool = _location_candidates(db, current_user.workspace_id)
    for m in mapped:
        loc_ref = m.get("location")
        resolved = None
        if payload.default_location_id is not None and not payload.column_map.location:
            resolved = payload.default_location_id
        else:
            resolved = _resolve_location_id(
                db, current_user.workspace_id, loc_ref, candidates=location_pool
            )
            if resolved is None and loc_ref is not None:
                dry_errors.append(
                    {
                        "row_index": m["row_index"],
                        "field": "location",
                        "message": f"Location '{loc_ref}' not found in workspace",
                    }
                )
        # Future-date check for preview.
        d = m.get("date")
        if isinstance(d, datetime.date) and d > datetime.datetime.now(datetime.UTC).date():
            dry_errors.append(
                {"row_index": m["row_index"], "field": "date", "message": "date is in the future"}
            )

    # Persist mapping for the commit step.
    preview["column_map"] = column_map
    preview["default_location_id"] = (
        str(payload.default_location_id) if payload.default_location_id else None
    )
    preview["dry_run"] = {
        "mapped_count": len(mapped),
        "error_count": len(dry_errors),
        "skipped_count": len(skipped),
    }
    upload.preview = preview  # type: ignore[assignment]
    # Ready when there is something importable and nothing broken. Skipped
    # lines (a TOTAL footer, a blank spacer) are not errors: refusing a whole
    # month's import over its footer is how a messy file becomes unimportable.
    upload.status = "ready" if mapped and not dry_errors else "awaiting_mapping"
    upload.error_count = len(dry_errors)
    db.flush()

    return {
        "upload_id": str(upload.id),
        "mapped_rows": len(mapped),
        "error_count": len(dry_errors),
        "errors": dry_errors[:20],
        # Rows the sheet had but that were never data (totals, blanks, "n/a").
        "skipped_count": len(skipped),
        "skipped": skipped[:20],
        "status": upload.status,
    }


@router.post("/file/{upload_id}/commit")
def file_commit(
    upload_id: uuid.UUID,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Commit staged file rows into ``demand_observations``."""
    upload: IngestUpload | None = db.get(IngestUpload, upload_id)
    if upload is None or upload.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Upload not found")

    if upload.status == "committed":
        raise _error(409, "already_committed", "Upload already committed")

    preview = upload.preview or {}
    column_map: dict[str, str] | None = preview.get("column_map")  # type: ignore[assignment]
    if not column_map:
        raise _error(422, "missing_mapping", "Column mapping not configured. Call mapping first")

    all_rows: list[dict[str, Any]] = preview.get("all_rows", [])  # type: ignore[assignment]
    default_loc_str: str | None = preview.get("default_location_id")  # type: ignore[assignment]
    default_loc = uuid.UUID(default_loc_str) if default_loc_str else None

    mapped, errors, skipped = apply_column_map(
        all_rows, column_map, default_location_id=default_loc
    )
    # Rows that could not be read are reported with the outcome rather than
    # aborting it: the days this file did contain still land, and the counts say
    # exactly what was left behind. Only a file with nothing importable is a
    # failure (checked below), which is the difference between "messy" and
    # "wrong" for a paper log transcribed into a spreadsheet.

    committed = 0
    reconciled = 0
    row_errors: list[dict[str, Any]] = []
    location_pool = _location_candidates(db, current_user.workspace_id)

    for m in mapped:
        raw_loc = m.get("location")
        if default_loc is not None and not column_map.get("location"):
            location_id = default_loc
        else:
            resolved = _resolve_location_id(
                db, current_user.workspace_id, raw_loc, candidates=location_pool
            )
            if resolved is None:
                row_errors.append(
                    {
                        "row_index": m["row_index"],
                        "field": "location",
                        "message": f"Location '{raw_loc}' not found",
                    }
                )
                continue
            location_id = resolved

        try:
            result = commit_demand(
                db,
                workspace_id=current_user.workspace_id,
                location_id=location_id,
                date=m["date"],
                demand_qty=m["demand_qty"],
                source="file",
                upload_id=upload.id,
                notes=m.get("notes"),
            )
            if result.created:
                committed += 1
            else:
                reconciled += 1
            _log_access(
                db,
                workspace_id=current_user.workspace_id,
                user=current_user,
                location_id=location_id,
                action="view",
                request=request,
            )
        except NormaliserError as exc:
            row_errors.append(
                {
                    "row_index": m["row_index"],
                    "field": exc.field,
                    "code": exc.code,
                    "message": exc.message,
                }
            )
        except Exception as exc:  # pragma: no cover
            row_errors.append(
                {
                    "row_index": m["row_index"],
                    "field": None,
                    "code": "internal_error",
                    "message": str(exc),
                }
            )

    all_errors = errors + row_errors
    if all_errors and committed + reconciled == 0:
        # Nothing was importable: this is a failed import, not a messy one.
        upload.error_count = len(all_errors)
        upload.status = "failed"
        db.flush()
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "commit_errors",
                    "message": f"No rows could be imported ({len(all_errors)} failed)",
                    "field": None,
                    "reference": getattr(request.state, "request_id", None),
                    "details": all_errors[:20],
                }
            },
        )

    upload.status = "committed"
    upload.error_count = len(all_errors)
    db.flush()

    return {
        "upload_id": str(upload.id),
        "committed": committed,
        "reconciled": reconciled,
        # Rows left behind, named by reason so the screen can say "3 rows had no
        # date or count" instead of silently dropping them.
        "skipped": len(skipped),
        "failed": len(all_errors),
        "errors": all_errors[:20],
        "status": upload.status,
    }


# ---------------------------------------------------------------------------
# Image: OCR (handwritten/printed log → reviewable rows)
# ---------------------------------------------------------------------------


@router.post("/image", status_code=status.HTTP_201_CREATED)
async def upload_image(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    file: Annotated[UploadFile, File(description="Handwritten log image")],
) -> dict[str, Any]:
    """Run OCR on an uploaded image and stage reviewable rows."""
    if not file.filename:
        raise _error(400, "validation_error", "Filename is required", field="file")

    data = await file.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise _error(400, "file_too_large", f"Image exceeds {MAX_IMAGE_BYTES} bytes", field="file")
    if not data:
        raise _error(400, "empty_file", "Image is empty", field="file")

    # The content decides, not the filename or the browser-provided MIME type.
    # Rejecting here keeps non-images out of the review queue and out of the
    # remote OCR service, instead of returning a job that can only fail later.
    try:
        info = validate_image_upload(data, file.filename)
    except ImageValidationError as exc:
        raise _error(400, "invalid_image", str(exc), field="file") from None

    preview = preview_for_image(data, file.filename)

    upload = IngestUpload(
        id=uuid.uuid4(),
        workspace_id=current_user.workspace_id,
        uploaded_by=current_user.id,
        kind="image",
        filename=file.filename,
        status="ready",
        row_count=preview.get("row_count", 0),
        error_count=0,
        preview=preview,
        # Keep the photo itself: the review screen asks the operator to check
        # every row against it, and without a stored copy that check ends when
        # the browser tab does. The MIME type comes from the bytes, never from
        # the client's Content-Type.
        source_image=data,
        source_mime=image_mime_type(info.format),
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db.add(upload)
    db.flush()

    logger.info("image staged job=%s workspace=%s", upload.id, current_user.workspace_id)

    return {"job_id": str(upload.id), "status": upload.status, "filename": file.filename}


@router.get("/image/{job_id}")
def get_image_job(
    job_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Return OCR status and extracted rows for review."""
    upload: IngestUpload | None = db.get(IngestUpload, job_id)
    if upload is None or upload.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Image job not found")
    if upload.kind != "image":
        raise _error(404, "not_found", "Not an image job")

    preview = upload.preview or {}
    return {
        "job_id": str(upload.id),
        "status": upload.status,
        "filename": upload.filename,
        "extracted_rows": preview.get("rows", []),
        "headers": preview.get("headers", []),
        "row_count": upload.row_count,
        # Which engine read the photo. A remote vision service returns a model
        # confidence; the local parser's value is a readability signal. The review
        # screen words each row from this rather than showing one number as if it
        # meant the same thing either way.
        "engine": preview.get("engine"),
        # Measured engine metadata (mean word confidence, page segmentation mode,
        # decoded dimensions). Kept separate from each row's confidence, which
        # for the local parser is a readability signal rather than a probability.
        "quality": preview.get("quality"),
        "note": preview.get("note"),
    }


@router.post("/image/{job_id}/commit")
def commit_image_job(
    job_id: uuid.UUID,
    payload: ImageCommitRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Commit corrected OCR rows (human-confirmed)."""
    upload: IngestUpload | None = db.get(IngestUpload, job_id)
    if upload is None or upload.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Image job not found")
    if upload.kind != "image":
        raise _error(404, "not_found", "Not an image job")
    if upload.status == "committed":
        raise _error(409, "already_committed", "Image job already committed")

    if not payload.rows:
        raise _error(400, "validation_error", "rows must be non-empty", field="rows")

    committed = 0
    reconciled = 0
    errors: list[dict[str, Any]] = []

    for idx, row in enumerate(payload.rows):
        try:
            result = commit_demand(
                db,
                workspace_id=current_user.workspace_id,
                location_id=row.location_id,
                date=row.date,
                demand_qty=row.demand_qty,
                source="image",
                upload_id=upload.id,
                notes=row.notes,
            )
            if result.created:
                committed += 1
            else:
                reconciled += 1
            _log_access(
                db,
                workspace_id=current_user.workspace_id,
                user=current_user,
                location_id=row.location_id,
                action="view",
                request=request,
            )
        except NormaliserError as exc:
            errors.append(
                {"row_index": idx, "field": exc.field, "code": exc.code, "message": exc.message}
            )
        except Exception as exc:  # pragma: no cover
            errors.append(
                {"row_index": idx, "field": None, "code": "internal_error", "message": str(exc)}
            )

    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "commit_errors",
                    "message": f"{len(errors)} row(s) failed to commit",
                    "field": None,
                    "reference": getattr(request.state, "request_id", None),
                    "details": errors,
                }
            },
        )

    upload.status = "committed"
    db.flush()

    return {
        "job_id": str(upload.id),
        "committed": committed,
        "reconciled": reconciled,
        "status": upload.status,
    }


# ---------------------------------------------------------------------------
# Demand observations — past fills (read-only, for the manual form)
# ---------------------------------------------------------------------------


@router.get("/demand")
def list_demand(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    location_id: uuid.UUID | None = Query(default=None),
    upload_id: uuid.UUID | None = Query(
        default=None, description="Only rows written by this staged upload"
    ),
    limit: int = Query(default=10, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List recent demand observations for the workspace (most recent first).

    Used by the manual form's "Past fills" preview so operators can see
    what is already stored before adding more, and by the spreadsheet tab to
    trace the rows a single import produced (``?upload_id=``).
    """
    q = select(DemandObservation).where(DemandObservation.workspace_id == current_user.workspace_id)
    if location_id is not None:
        loc = db.get(Location, location_id)
        if loc is None or loc.workspace_id != current_user.workspace_id:
            raise _error(404, "not_found", "Location not found", field="location_id")
        q = q.where(DemandObservation.location_id == location_id)
    if upload_id is not None:
        # Rows are already workspace-scoped above, so an upload id from another
        # workspace simply matches nothing — no separate ownership check needed
        # to keep this from leaking across tenants.
        q = q.where(DemandObservation.upload_id == upload_id)
    q = (
        q.order_by(DemandObservation.date.desc(), DemandObservation.id.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = list(db.scalars(q).all())

    # Provenance: which staged file each row came from. Resolved in one query for
    # the distinct ids on this page rather than a JOIN, because a page carries at
    # most `limit` rows and normally one upload.
    filenames: dict[uuid.UUID, str] = {}
    upload_ids = {r.upload_id for r in rows if r.upload_id is not None}
    if upload_ids:
        filenames = {
            u.id: u.filename
            for u in db.scalars(select(IngestUpload).where(IngestUpload.id.in_(upload_ids))).all()
        }

    return {
        "data": [
            {
                "id": r.id,
                "location_id": str(r.location_id),
                "date": r.date.isoformat(),
                "demand_qty": r.demand_qty,
                "notes": r.notes,
                # Rows written before the tag was consistently bracketed carry
                # ``source:file`` with no brackets. Both are read, so provenance
                # survives the fix instead of vanishing for old rows.
                "source": _source_tag(r.notes),
                # `null` for a manual/device row: those never came from a file, and
                # naming a file for them would invent a provenance they do not have.
                "upload_id": str(r.upload_id) if r.upload_id is not None else None,
                "upload_filename": filenames.get(r.upload_id) if r.upload_id is not None else None,
                "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
            }
            for r in rows
        ],
        "total": len(rows),
    }


class DemandPatch(BaseModel):
    """Patch payload for a single demand observation (all fields optional)."""

    demand_qty: int | None = Field(default=None, ge=0)
    date: datetime.date | None = None
    notes: str | None = Field(default=None, max_length=500)
    location_id: uuid.UUID | None = None


@router.patch("/demand/{observation_id}")
def patch_demand(
    observation_id: int,
    payload: DemandPatch,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Update a single demand observation (workspace-scoped)."""
    obs: DemandObservation | None = db.get(DemandObservation, observation_id)
    if obs is None or obs.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Demand observation not found")
    # Resolve target values
    target_date = payload.date if payload.date is not None else obs.date
    target_qty = payload.demand_qty if payload.demand_qty is not None else obs.demand_qty
    target_notes = payload.notes if payload.notes is not None else obs.notes
    target_location = payload.location_id if payload.location_id is not None else obs.location_id

    # Validate quantity & future date via normaliser rules — the PATCH path
    # writes the column directly, so it has to borrow the same gate rather than
    # re-state it (a second copy is how the two paths drift apart).
    try:
        validate_demand_qty(target_qty)
    except NormaliserError as exc:
        raise _error(422, exc.code, exc.message, field=exc.field) from None
    if target_date > datetime.datetime.now(datetime.UTC).date():
        raise _error(
            422, "future_date", f"date {target_date.isoformat()} is in the future", field="date"
        )
    # Validate location if changed
    if target_location != obs.location_id:
        loc = db.get(Location, target_location)
        if (
            loc is None
            or loc.workspace_id != current_user.workspace_id
            or loc.archived_at is not None
        ):
            raise _error(404, "not_found", "Location not found", field="location_id")
        # Check uniqueness conflict on (location_id, date) if either changed
        existing = db.scalar(
            select(DemandObservation).where(
                DemandObservation.location_id == target_location,
                DemandObservation.date == target_date,
                DemandObservation.id != obs.id,
            )
        )
        if existing is not None:
            raise _error(
                409, "conflict", "Another observation already exists for this location and date"
            )
    elif payload.date is not None and payload.date != obs.date:
        existing = db.scalar(
            select(DemandObservation).where(
                DemandObservation.location_id == obs.location_id,
                DemandObservation.date == target_date,
                DemandObservation.id != obs.id,
            )
        )
        if existing is not None:
            raise _error(
                409, "conflict", "Another observation already exists for this location and date"
            )

    obs.date = target_date  # type: ignore[assignment]
    obs.demand_qty = target_qty  # type: ignore[assignment]
    obs.notes = target_notes
    obs.location_id = target_location  # type: ignore[assignment]
    obs.recorded_at = datetime.datetime.now(datetime.UTC)
    db.flush()
    return {
        "id": obs.id,
        "location_id": str(obs.location_id),
        "date": obs.date.isoformat(),
        "demand_qty": obs.demand_qty,
        "notes": obs.notes,
        "recorded_at": obs.recorded_at.isoformat() if obs.recorded_at else None,
    }


@router.delete("/demand/{observation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_demand(
    observation_id: int,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    """Delete a single demand observation (workspace-scoped)."""
    obs: DemandObservation | None = db.get(DemandObservation, observation_id)
    if obs is None or obs.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Demand observation not found")
    db.delete(obs)
    db.flush()


# ---------------------------------------------------------------------------
# Uploads list / delete
# ---------------------------------------------------------------------------


@router.get("/uploads")
def list_uploads(
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    kind: str | None = Query(default=None, description="Filter by kind: csv|xlsx|image"),
    status_filter: str | None = Query(default=None, alias="status", description="Filter by status"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> dict[str, Any]:
    """Recent uploads for the workspace (most recent first)."""
    q = select(IngestUpload, _has_source_expr()).where(
        IngestUpload.workspace_id == current_user.workspace_id
    )

    if kind is not None:
        q = q.where(IngestUpload.kind == kind)
    if status_filter is not None:
        q = q.where(IngestUpload.status == status_filter)

    # Simple cursor: created_at < cursor timestamp (ISO string)
    if cursor:
        try:
            ts = datetime.datetime.fromisoformat(cursor)
            q = q.where(IngestUpload.created_at < ts)
        except ValueError:
            raise _error(400, "invalid_cursor", "Invalid cursor") from None

    q = q.order_by(IngestUpload.created_at.desc()).limit(limit + 1)
    rows = db.execute(q).all()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = page[-1][0].created_at.isoformat() if has_more and page else None

    return {
        "data": [
            {
                "id": str(r.id),
                "kind": r.kind,
                "filename": r.filename,
                "status": r.status,
                "row_count": r.row_count,
                "error_count": r.error_count,
                # Only ever a flag: the bytes are deferred on the model and are
                # fetched from /uploads/{id}/source when someone asks to see them.
                "has_source": bool(has_source),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r, has_source in page
        ],
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


@router.get("/uploads/{upload_id}/source")
def get_upload_source(
    upload_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    """Return the original photo an image upload was read from.

    This is what makes an OCR row checkable after the fact: the preview holds
    the parsed values, and only the photo says whether they are right. Uploads
    from before photos were kept, and non-image uploads, have nothing to serve
    and answer 404 rather than an empty image the browser would render broken.

    Read access is the router's ingest permission plus the workspace check — the
    same boundary as the rows themselves, since the photo contains them.
    """
    upload: IngestUpload | None = db.get(IngestUpload, upload_id)
    if upload is None or upload.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Upload not found")
    if upload.source_image is None:
        raise _error(404, "no_source", "This upload has no stored original")

    return Response(
        content=upload.source_image,
        media_type=upload.source_mime or "application/octet-stream",
        headers={
            "Content-Disposition": f'inline; filename="{_download_name(upload.filename)}"',
            # Private and briefly cacheable: the content for a given id never
            # changes, but it is workspace data and must not land in a shared
            # cache.
            "Cache-Control": "private, max-age=300",
            # The bytes are a user upload served from the API origin; stopping
            # content sniffing keeps a mislabelled file from being treated as
            # anything but the image type that was stored.
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/uploads/{upload_id}/retry")
def retry_upload(
    upload_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Read a stored original again, replacing the staged rows.

    Only possible for uploads whose bytes were kept, which today means photos.
    A spreadsheet's parsed result is what is stored, not the sheet, so the
    caller is told ``no_source`` and can fall back to picking the file again.
    """
    upload: IngestUpload | None = db.get(IngestUpload, upload_id)
    if upload is None or upload.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Upload not found")
    if upload.status == "committed":
        # Its rows are already history. Re-reading would stage a second set
        # under the same import and leave the first unattributable; the delete
        # action is the way back from a committed import.
        raise _error(409, "already_committed", "This import is already committed")
    if upload.source_image is None:
        raise _error(409, "no_source", "The original file was not kept for this upload")

    preview = preview_for_image(upload.source_image, upload.filename)
    # A new dict, not a mutation: a JSONB column is only saved when the value is
    # assigned (the MutableJSONB in-place bug that once dropped column maps).
    upload.preview = preview
    upload.row_count = preview.get("row_count", 0)
    upload.error_count = 0
    upload.status = "ready"
    db.flush()

    logger.info("upload retried job=%s rows=%s", upload.id, upload.row_count)
    return {
        "job_id": str(upload.id),
        "row_count": upload.row_count,
        "engine": preview.get("engine"),
        "status": upload.status,
    }


@router.delete("/uploads/{upload_id}")
def delete_upload(
    upload_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """Delete an upload, and the demand rows it wrote.

    This is the undo for an import. It used to refuse once anything had been
    committed, which left an operator who noticed a bad import with no way back:
    the entries existed, but the record that explained them could not be
    removed, and the screen offered no action at all on the row.

    Deleting the observations is the same decision, not a side effect: their
    provenance is going away, and leaving them behind would keep numbers in
    demand history that nothing can explain. The count is returned so the caller
    can say exactly what was removed.
    """
    upload: IngestUpload | None = db.get(IngestUpload, upload_id)
    if upload is None or upload.workspace_id != current_user.workspace_id:
        raise _error(404, "not_found", "Upload not found")

    filename = upload.filename
    # Rows first: the FK from demand_observations would reject the upload
    # delete, and a cascade would be invisible to the caller's report.
    removed = db.execute(
        sa_delete(DemandObservation).where(DemandObservation.upload_id == upload.id)
    ).rowcount
    db.delete(upload)
    db.flush()

    removed_rows = int(removed or 0)
    logger.info("upload deleted job=%s rows=%s", upload_id, removed_rows)
    return {"deleted": True, "filename": filename, "removed_rows": removed_rows}
