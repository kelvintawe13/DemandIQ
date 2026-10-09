"""Image ingestion — OCR extraction for handwritten/printed demand logs.

The adapter turns a photo of an operator's log into candidate
``{date, demand_qty}`` rows for human confirmation before commit
(``ARCHITECTURE.md`` §"Handwritten photo → OCR → human-confirmed"):

1. **Remote OCR/vision service** — when ``OCR_SERVICE_URL`` is configured the
   image is POSTed there and the service returns JSON rows.
2. **Local Tesseract** — used automatically when ``pytesseract`` and
   ``Pillow`` are installed (``pip install 'demandiq-backend[ocr]'``).
3. **Not configured** — returns no rows plus an explicit note telling the
   operator to type them in.

The adapter never invents rows. A fabricated preview that an operator might
commit without reading would silently corrupt the demand history the models
are trained on, so an honest empty preview is the correct failure mode.
"""

from __future__ import annotations

import datetime
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

# Ordered most-specific first: ISO (Y-M-D), then D/M/Y, then two-digit year.
_ISO_DATE = re.compile(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b")
_DMY_DATE = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b")
_DMY2_DATE = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2})\b")
_TEXT_DATE = re.compile(
    r"\b(\d{1,2})[-/.\s]?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[-/.\s]?(20\d{2})?\b",
    re.IGNORECASE,
)
_INT = re.compile(r"(?<!\d)(\d{1,6})(?!\d)")

# What `parse_ocr_text` puts in a row's `confidence`, and what those numbers mean.
# They describe how legible the line was, not how likely the value is correct.
CONFIDENCE_UNAMBIGUOUS_LINE = 0.9
CONFIDENCE_AMBIGUOUS_LINE = 0.75


def _safe_date(year: str | int, month: str | int, day: str | int) -> datetime.date | None:
    try:
        return datetime.date(int(year), int(month), int(day))
    except (ValueError, TypeError):
        return None


def _find_date(line: str) -> tuple[datetime.date | None, tuple[int, int] | None]:
    """Return the first recognisable date on a line and its character span."""
    for pattern, kind in (
        (_ISO_DATE, "iso"),
        (_DMY_DATE, "dmy"),
        (_TEXT_DATE, "text"),
        (_DMY2_DATE, "dmy2"),
    ):
        match = pattern.search(line)
        if not match:
            continue
        if kind == "iso":
            date = _safe_date(match.group(1), match.group(2), match.group(3))
        elif kind == "dmy":
            date = _safe_date(match.group(3), match.group(2), match.group(1))
        elif kind == "dmy2":
            year = int(match.group(3))
            year += 2000 if year < 70 else 1900
            date = _safe_date(year, match.group(2), match.group(1))
        else:  # text month, e.g. "3 Jan 2026" or "3 Jan"
            month = _MONTHS.get(match.group(2).lower()[:3])
            year_raw = match.group(3)
            year = int(year_raw) if year_raw else datetime.datetime.now(datetime.UTC).year
            date = _safe_date(year, month or 0, match.group(1))
        if date is not None:
            return date, match.span()
    return None, None


def parse_ocr_text(text: str) -> list[dict[str, Any]]:
    """Parse OCR text into candidate ``{date, demand_qty}`` rows.

    Each output line is expected to carry one date and one demand count,
    typically ``"12/01/2026   112"``. The date is stripped first and the last
    standalone integer left on the line becomes the demand quantity, which
    tolerates trailing notes. Lines without both a date and a count are
    skipped rather than guessed at.

    Args:
        text: Raw OCR output (may be multi-column; lines are read left to
            right).

    Returns:
        Candidate rows with ``row_index``, ISO ``date``, ``demand_qty``,
        ``confidence`` and provenance in ``notes``.
    """
    rows: list[dict[str, Any]] = []
    idx = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        date, span = _find_date(line)
        if date is None or span is None:
            continue
        remainder = line[: span[0]] + " " + line[span[1] :]
        counts = _INT.findall(remainder)
        if not counts:
            continue
        demand_qty = int(counts[-1])
        if demand_qty <= 0:
            continue
        # A readability signal, not a calibrated probability. Exactly one number
        # left on the line means the count was unambiguous; several means the last
        # one was chosen for the operator, which is a guess worth flagging. The
        # remote service supplies its own real score instead — `engine` on the
        # preview records which of the two this is.
        confidence = CONFIDENCE_UNAMBIGUOUS_LINE if len(counts) == 1 else CONFIDENCE_AMBIGUOUS_LINE
        rows.append(
            {
                "row_index": idx,
                "date": date.isoformat(),
                "demand_qty": demand_qty,
                "location": None,
                "notes": None,
                "confidence": confidence,
            }
        )
        idx += 1
    return rows


def _extract_via_remote(content: bytes, filename: str, service_url: str) -> list[dict[str, Any]]:
    """POST the image to a configured OCR/vision service and parse its rows."""
    resp = httpx.post(
        service_url,
        files={"file": (filename, content)},
        timeout=30.0,
    )
    resp.raise_for_status()
    payload = resp.json()
    raw_rows = payload.get("rows", payload if isinstance(payload, list) else [])
    rows: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_rows):
        if not isinstance(item, dict):
            continue
        date_raw = item.get("date")
        qty_raw = item.get("demand_qty", item.get("quantity", item.get("count")))
        if date_raw is None or qty_raw is None:
            continue
        try:
            date = datetime.date.fromisoformat(str(date_raw))
            demand_qty = int(qty_raw)
        except (TypeError, ValueError):
            continue
        if demand_qty <= 0:
            continue
        rows.append(
            {
                "row_index": idx,
                "date": date.isoformat(),
                "demand_qty": demand_qty,
                "location": None,
                "notes": None,
                "confidence": float(item.get("confidence", 0.9)),
            }
        )
    return rows


def _extract_via_tesseract(content: bytes, language: str) -> list[dict[str, Any]]:
    """Run local Tesseract OCR (raises ImportError when not installed)."""
    import io

    import pytesseract
    from PIL import Image

    image = Image.open(io.BytesIO(content))
    text = pytesseract.image_to_string(image, lang=language)
    return parse_ocr_text(text)


# Which engine produced an extraction. The screen needs this because the two
# engines mean different things by `confidence` — see :func:`extract_rows_with_engine`.
ENGINE_REMOTE = "remote"
ENGINE_TESSERACT = "tesseract"
ENGINE_NONE = "none"


@dataclass(frozen=True, slots=True)
class ImageExtraction:
    """Candidate rows plus which engine read them, and why if none did."""

    rows: list[dict[str, Any]]
    engine: str
    detail: str | None = None


def extract_rows_with_engine(content: bytes, filename: str) -> ImageExtraction:
    """Extract candidate demand rows from an image, using the best engine.

    Tries the configured remote service, then local Tesseract, and otherwise
    returns no rows (never fabricated ones). Engine failures are logged and
    degrade to an empty extraction so the operator can still type rows in.

    The reported engine matters to the caller: a remote vision service returns a
    *model* confidence, while the local parser's ``confidence`` is a readability
    signal — how many numbers were left on the line after the date, i.e. whether
    picking the demand count was unambiguous. Presenting the second as a
    percentage would dress a heuristic up as a probability, so the engine travels
    with the rows and the screen labels each accordingly.
    """
    settings = get_settings()
    service_url = getattr(settings, "ocr_service_url", None)

    if service_url:
        try:
            rows = _extract_via_remote(content, filename, service_url)
            logger.info("remote OCR extracted %s rows from %s", len(rows), filename)
            return ImageExtraction(rows=rows, engine=ENGINE_REMOTE)
        except Exception as exc:
            logger.warning("remote OCR service failed for %s", filename, exc_info=True)
            remote_failure = str(exc) or exc.__class__.__name__
    else:
        remote_failure = None

    try:
        rows = _extract_via_tesseract(content, getattr(settings, "ocr_language", "eng"))
        logger.info("tesseract OCR extracted %s rows from %s", len(rows), filename)
        return ImageExtraction(rows=rows, engine=ENGINE_TESSERACT)
    except ImportError:
        logger.info(
            "OCR engine not installed. No rows extracted from %s "
            "(install 'demandiq-backend[ocr]' or set OCR_SERVICE_URL)",
            filename,
        )
        if remote_failure is not None:
            return ImageExtraction(
                rows=[],
                engine=ENGINE_NONE,
                detail=f"The OCR service could not be reached ({remote_failure}) and no local engine is installed.",
            )
        return ImageExtraction(rows=[], engine=ENGINE_NONE, detail="No OCR engine is configured.")
    except Exception as exc:
        logger.warning("local OCR failed for %s", filename, exc_info=True)
        detail = f"Local OCR failed ({str(exc) or exc.__class__.__name__})."
        if remote_failure is not None:
            detail = f"The OCR service could not be reached ({remote_failure}) and {detail[0].lower()}{detail[1:]}"
        return ImageExtraction(rows=[], engine=ENGINE_NONE, detail=detail)


def extract_rows_from_image(content: bytes, filename: str) -> list[dict[str, Any]]:
    """The candidate rows alone, for callers that do not need the engine."""
    return extract_rows_with_engine(content, filename).rows


def _note_for(extraction: ImageExtraction) -> str:
    if extraction.rows:
        return "OCR extraction complete. Review every row before committing."
    if extraction.detail:
        return f"{extraction.detail} Add the rows manually, or retry with a clearer photo."
    return (
        "No readable rows were found in this photo. Add the rows manually, or retry with a "
        "clearer photo."
    )


def preview_for_image(content: bytes, filename: str) -> dict[str, Any]:
    """Build the preview payload stored on ``ingest_uploads.preview`` for images."""
    extraction = extract_rows_with_engine(content, filename)
    return {
        "headers": ["date", "demand_qty", "location"],
        "rows": extraction.rows,
        "row_count": len(extraction.rows),
        # Travels with the rows so the review screen can word `confidence` for the
        # engine that actually produced it (see `extract_rows_with_engine`).
        "engine": extraction.engine,
        "note": _note_for(extraction),
        "extracted_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
