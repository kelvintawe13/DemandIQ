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
import io
import logging
import re
from dataclasses import dataclass, field
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
# Every numeric date, used only to infer whether the sheet is day-first or
# month-first before rows are parsed. The lookarounds keep this from matching
# the tail of an ISO date (the "26-01-12" inside "2026-01-12").
_NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})(?!\d)")
_TEXT_DATE = re.compile(
    r"\b(\d{1,2})[-/.\s]?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[-/.\s]?(20\d{2})?\b",
    re.IGNORECASE,
)
# US-style logs put the month first ("Oct 1", "October 12, 2026"). The day
# cannot start inside a bare year: without the lookbehind, the first two digits
# of "October 2026" would be read as a day and produce a row that was never on
# the page.
_TEXT_DATE_MONTH_FIRST = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[\s./-]*(?<!\d)([lI|]\d?|\d{1,2})(?!\d)"
    r"(?:[\s,./-]+(20\d{2}))?",
    re.IGNORECASE,
)
_INT = re.compile(r"(?<!\d)(\d{1,6})(?!\d)")

# Tesseract frequently reads a printed/handwritten 7 as '/' when it ends a
# count token ("2/" for 27). Only the unambiguous single-digit-before-slash
# form is corrected; a date's "2/05" is left alone because a digit follows,
# and the date itself has already been removed from the line at this point.
_TRAILING_SEVEN = re.compile(r"(?<!\d)(\d)[/|\\](?!\d)")

# What `parse_ocr_text` puts in a row's `confidence`, and what those numbers mean.
# They describe how legible the line was, not how likely the value is correct.
CONFIDENCE_UNAMBIGUOUS_LINE = 0.9
CONFIDENCE_AMBIGUOUS_LINE = 0.75

# Pre-processing is deliberately conservative: phone photos are usually large
# enough but skewed or low-contrast, while a tiny thumbnail is unreadable at any
# contrast. Upscaling below 800 px on the short edge and normalising contrast is
# enough to help Tesseract without inventing strokes in handwriting.
_UPSCALE_BELOW_EDGE = 800
_UPSCALE_TO_EDGE = 1600
_MAX_UPSCALE = 3.0


class ImageValidationError(ValueError):
    """Raised when an upload is not a decodable image within the size budget."""


@dataclass(frozen=True, slots=True)
class ImageInfo:
    """What could be established about an upload before OCR runs."""

    width: int | None
    height: int | None
    format: str | None


def _sniff_image_format(content: bytes) -> str | None:
    """Identify common image formats from their signature bytes.

    This works without Pillow, so an upload is rejected as a non-image even in
    the default image (where OCR is unavailable). It is a *format check*, not a
    decode; :func:`validate_image_upload` decodes when Pillow is installed.
    """
    signatures: tuple[tuple[bytes, str], ...] = (
        (b"\xff\xd8\xff", "jpeg"),
        (b"\x89PNG\r\n\x1a\n", "png"),
        (b"GIF87a", "gif"),
        (b"GIF89a", "gif"),
        (b"BM", "bmp"),
        (b"II*\x00", "tiff"),
        (b"MM\x00*", "tiff"),
    )
    for signature, fmt in signatures:
        if content.startswith(signature):
            return fmt
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    if len(content) >= 12 and content[4:8] == b"ftyp":
        brand = content[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1"):
            return "heic"
    return None


def validate_image_upload(content: bytes, filename: str | None = None) -> ImageInfo:
    """Validate and characterise an uploaded image before OCR.

    Two checks matter for the ingestion boundary: the bytes are actually an
    image (not a renamed executable or arbitrary text), and the decoded pixel
    count is bounded so a tiny compressed file cannot expand into a
    decompression bomb while Tesseract works on it. Pillow is optional; without
    it the signature check still rejects non-images, but dimensions cannot be
    reported.

    Args:
        content: Raw uploaded bytes.
        filename: Original filename, used only in error messages.

    Returns:
        Dimensions and detected format, with ``None`` dimensions when Pillow is
        not installed.

    Raises:
        ImageValidationError: If the bytes are empty, not a recognised image, or
            cannot be decoded / exceed the configured pixel budget.
    """
    if not content:
        raise ImageValidationError("Image is empty")

    sniffed = _sniff_image_format(content)
    if sniffed is None:
        label = f" '{filename}'" if filename else ""
        raise ImageValidationError(
            f"File{label} does not look like an image (expected JPEG, PNG, WebP, GIF, BMP or TIFF)"
        )

    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        return ImageInfo(width=None, height=None, format=sniffed)

    max_pixels = int(get_settings().ocr_max_pixels)
    try:
        with Image.open(io.BytesIO(content)) as opened:
            width, height = opened.size
            if width <= 0 or height <= 0:
                raise ImageValidationError("Image has no usable dimensions")
            if width * height > max_pixels:
                raise ImageValidationError(
                    f"Image is {width}x{height} ({width * height:,} pixels); "
                    f"the limit is {max_pixels:,} pixels"
                )
            fmt = (opened.format or sniffed).lower()
            # ``verify`` checks the file structure without decoding every pixel;
            # a reopened handle is used later for actual OCR work.
            opened.verify()
    except ImageValidationError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise ImageValidationError(f"Image could not be decoded ({exc})") from exc
    except Exception as exc:  # decompression-bomb and decoder errors vary by version
        raise ImageValidationError(f"Image could not be validated ({exc})") from exc

    return ImageInfo(width=width, height=height, format=fmt)


def _prepare_image(content: bytes):  # type: ignore[no-untyped-def]
    """Decode, orient, upscale and normalise an image for Tesseract.

    EXIF rotation is applied first: a portrait phone photo stored landscape is
    the most common way OCR silently reads nothing. Contrast is stretched and a
    white border added, both of which help Tesseract's layout analysis; no
    thresholding is applied because binarising handwriting can erase the very
    strokes that distinguish a 7 from a 1.
    """
    from PIL import Image, ImageOps

    max_pixels = int(get_settings().ocr_max_pixels)
    with Image.open(io.BytesIO(content)) as opened:
        width, height = opened.size
        if width * height > max_pixels:
            raise ImageValidationError(
                f"Image is {width}x{height} ({width * height:,} pixels); "
                f"the limit is {max_pixels:,} pixels"
            )
        opened.load()
        image = ImageOps.exif_transpose(opened)

    if image.mode not in ("L", "RGB"):
        image = image.convert("RGB")

    width, height = image.size
    shortest = min(width, height)
    if shortest and shortest < _UPSCALE_BELOW_EDGE:
        factor = min(_UPSCALE_TO_EDGE / shortest, _MAX_UPSCALE)
        if factor > 1.05:
            resample = getattr(Image, "Resampling", Image).LANCZOS
            image = image.resize(
                (max(1, int(width * factor)), max(1, int(height * factor))),
                resample,
            )

    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    # A small quiet border keeps text away from the crop edge, where Tesseract's
    # layout analysis otherwise drops the first/last line of a tight photo.
    return ImageOps.expand(gray, border=16, fill=255)


def _safe_date(year: str | int, month: str | int, day: str | int) -> datetime.date | None:
    try:
        return datetime.date(int(year), int(month), int(day))
    except (ValueError, TypeError):
        return None


def _normalise_year(raw: str | int) -> int:
    """Expand a two-digit year the same way the numeric patterns do."""
    year = int(raw)
    if len(str(raw)) == 2:
        year += 2000 if year < 70 else 1900
    return year


def _numeric_dates(
    first: int, second: int, year: int
) -> tuple[datetime.date | None, datetime.date | None]:
    """Both plausible readings of ``first/second/year``: (day-first, month-first)."""
    return (
        _safe_date(year, second, first),
        _safe_date(year, first, second),
    )


def _numeric_date(first: int, second: int, year: int, *, day_first: bool) -> datetime.date | None:
    """Choose the numeric reading, falling back when one order is invalid."""
    day_first_date, month_first_date = _numeric_dates(first, second, year)
    if day_first:
        return day_first_date or month_first_date
    return month_first_date or day_first_date


def _infer_day_first(text: str) -> bool:
    """Infer the sheet's numeric date order from dates that cannot be both.

    ``12/31`` can only be month-first and ``31/12`` can only be day-first. If a
    sheet contains one of these, use that order for its genuinely ambiguous
    rows too. A sheet with no such evidence keeps the app's day-first default,
    which is also what the ingestion footer tells the operator.
    """
    day_first_votes = 0
    month_first_votes = 0
    for match in _NUMERIC_DATE.finditer(text):
        first, second = int(match.group(1)), int(match.group(2))
        year = _normalise_year(match.group(3))
        day_first_date, month_first_date = _numeric_dates(first, second, year)
        if day_first_date and not month_first_date:
            day_first_votes += 1
        elif month_first_date and not day_first_date:
            month_first_votes += 1
    return day_first_votes >= month_first_votes


def _find_date(
    line: str, *, day_first: bool = True
) -> tuple[datetime.date | None, tuple[int, int] | None]:
    """Return the first recognisable date on a line and its character span."""
    for pattern, kind in (
        (_ISO_DATE, "iso"),
        (_DMY_DATE, "numeric"),
        (_TEXT_DATE, "text"),
        (_TEXT_DATE_MONTH_FIRST, "text_month_first"),
        (_DMY2_DATE, "numeric2"),
    ):
        match = pattern.search(line)
        if not match:
            continue
        if kind == "iso":
            date = _safe_date(match.group(1), match.group(2), match.group(3))
        elif kind == "numeric":
            date = _numeric_date(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                day_first=day_first,
            )
        elif kind == "numeric2":
            date = _numeric_date(
                int(match.group(1)),
                int(match.group(2)),
                _normalise_year(match.group(3)),
                day_first=day_first,
            )
        elif kind == "text":  # day first, e.g. "3 Jan 2026" or "3 Jan"
            month = _MONTHS.get(match.group(2).lower()[:3])
            year_raw = match.group(3)
            year = int(year_raw) if year_raw else datetime.datetime.now(datetime.UTC).year
            date = _safe_date(year, month or 0, match.group(1))
        else:  # month first, e.g. "Oct 1" or "October 12, 2026"
            month = _MONTHS.get(match.group(1).lower()[:3])
            # Tesseract commonly reads a handwritten/printed 1 as l, I or |.
            # That turns "Oct 1" into "Oct l", which is still a date to a human.
            day = int(re.sub(r"[lI|]", "1", match.group(2)))
            year_raw = match.group(3)
            year = int(year_raw) if year_raw else datetime.datetime.now(datetime.UTC).year
            date = _safe_date(year, month or 0, day)
        if date is not None:
            return date, match.span()
    return None, None


def parse_ocr_text(text: str, *, day_first: bool | None = None) -> list[dict[str, Any]]:
    """Parse OCR text into candidate ``{date, demand_qty}`` rows.

    Each output line is expected to carry one date and one demand count,
    typically ``"12/01/2026   112"``. The date is stripped first and the last
    standalone integer left on the line becomes the demand quantity, which
    tolerates trailing notes. Lines without both a date and a count are
    skipped rather than guessed at.

    Numeric dates are accepted in both day-first and month-first order when
    one reading is invalid (``31/12/2026`` and ``12/31/2026`` both parse).
    If both readings are valid, ``day_first`` decides; when it is omitted the
    sheet's unambiguous dates decide, and a sheet with no such evidence is
    read day-first. Callers that know their locale can pass it explicitly.

    Args:
        text: Raw OCR output (may be multi-column; lines are read left to
            right).
        day_first: Force day-first or month-first parsing for ambiguous
            numeric dates. ``None`` infers it from the sheet.

    Returns:
        Candidate rows with ``row_index``, ISO ``date``, ``demand_qty``,
        ``confidence`` and provenance in ``notes``.
    """
    if day_first is None:
        day_first = _infer_day_first(text)
    rows: list[dict[str, Any]] = []
    idx = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        date, span = _find_date(line, day_first=day_first)
        if date is None or span is None:
            continue
        remainder = line[: span[0]] + " " + line[span[1] :]
        corrected_glyph = bool(_TRAILING_SEVEN.search(remainder))
        if corrected_glyph:
            remainder = _TRAILING_SEVEN.sub(r"\g<1>7", remainder)
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
        confidence = (
            CONFIDENCE_UNAMBIGUOUS_LINE
            if len(counts) == 1 and not corrected_glyph
            else CONFIDENCE_AMBIGUOUS_LINE
        )
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


def _page_segmentation_modes() -> list[int]:
    """The Tesseract page-segmentation modes to try, in order.

    ``6`` assumes a uniform block of text and is the best default for a written
    log. ``4`` and ``11`` cover multi-column and sparse layouts; running all
    three and keeping the extraction with the most rows is a cheap way to be
    robust to an unfamiliar page without pulling in a layout model.
    """
    raw = str(getattr(get_settings(), "ocr_psm_modes", "6,4,11"))
    modes: list[int] = []
    for part in raw.split(","):
        try:
            mode = int(part.strip())
        except (TypeError, ValueError):
            continue
        if 0 <= mode <= 13 and mode not in modes:
            modes.append(mode)
    return modes or [6]


def _mean_word_confidence(
    image, language: str, psm: int, timeout: float  # type: ignore[no-untyped-def]
) -> float | None:
    """Mean Tesseract word confidence for one segmentation mode (0-1).

    Word confidence is a real engine score, unlike the line-legibility value in
    each row; the preview reports it separately so the two are never conflated.
    """
    import pytesseract

    try:
        data = pytesseract.image_to_data(
            image,
            lang=language,
            config=f"--psm {psm}",
            output_type=pytesseract.Output.DICT,
            timeout=timeout,
        )
    except TypeError:  # pytesseract < 0.3.10 has no timeout parameter
        data = pytesseract.image_to_data(
            image,
            lang=language,
            config=f"--psm {psm}",
            output_type=pytesseract.Output.DICT,
        )
    scores: list[float] = []
    for raw in data.get("conf", []):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value >= 0:
            scores.append(value)
    if not scores:
        return None
    return round(sum(scores) / len(scores) / 100.0, 4)


def _tesseract_extraction(content: bytes, language: str) -> ImageExtraction:
    """Run local Tesseract with preprocessing and several layout modes.

    Raises:
        ImportError: If ``pytesseract`` or Pillow is not installed.
        ImageValidationError: If the image cannot be decoded.
        RuntimeError: If every segmentation mode fails at the engine level.
    """
    import pytesseract

    image = _prepare_image(content)
    timeout = float(getattr(get_settings(), "ocr_timeout_seconds", 30.0))
    best: ImageExtraction | None = None
    failures: list[str] = []

    for psm in _page_segmentation_modes():
        try:
            text = pytesseract.image_to_string(
                image, lang=language, config=f"--psm {psm}", timeout=timeout
            )
        except TypeError:  # pytesseract < 0.3.10 has no timeout parameter
            text = pytesseract.image_to_string(image, lang=language, config=f"--psm {psm}")
        except Exception as exc:  # a failed mode must not discard the others
            failures.append(f"psm {psm}: {exc}")
            continue

        rows = parse_ocr_text(text)
        if not rows:
            continue
        try:
            mean_confidence = _mean_word_confidence(image, language, psm, timeout)
        except Exception:
            mean_confidence = None
        candidate = ImageExtraction(
            rows=rows,
            engine=ENGINE_TESSERACT,
            quality={
                "page_segmentation_mode": psm,
                "mean_confidence": mean_confidence,
                "rows": len(rows),
            },
        )
        if best is None:
            best = candidate
            continue
        best_score = (len(best.rows), best.quality.get("mean_confidence") or 0.0)
        candidate_score = (len(rows), mean_confidence or 0.0)
        if candidate_score > best_score:
            best = candidate

    if best is not None:
        return best
    if failures:
        raise RuntimeError("; ".join(failures))
    return ImageExtraction(
        rows=[],
        engine=ENGINE_TESSERACT,
        detail="No readable rows were found in this photo.",
        quality={"page_segmentation_modes": _page_segmentation_modes(), "rows": 0},
    )


def _extract_via_tesseract(content: bytes, language: str) -> list[dict[str, Any]]:
    """The extracted rows alone, kept for callers that do not need quality."""
    return _tesseract_extraction(content, language).rows


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
    quality: dict[str, Any] = field(default_factory=dict)


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
            scored = [float(r["confidence"]) for r in rows if r.get("confidence") is not None]
            return ImageExtraction(
                rows=rows,
                engine=ENGINE_REMOTE,
                quality={
                    "mean_confidence": round(sum(scored) / len(scored), 4) if scored else None,
                    "rows": len(rows),
                },
            )
        except Exception as exc:
            logger.warning("remote OCR service failed for %s", filename, exc_info=True)
            remote_failure = str(exc) or exc.__class__.__name__
    else:
        remote_failure = None

    try:
        extraction = _tesseract_extraction(content, getattr(settings, "ocr_language", "eng"))
        logger.info("tesseract OCR extracted %s rows from %s", len(extraction.rows), filename)
        return extraction
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
    """Build the preview payload stored on ``ingest_uploads.preview`` for images.

    Invalid bytes still produce a well-formed preview (never an exception): the
    upload route rejects them with a 400, while a direct caller gets the same
    honest empty result with the reason. The review screen must never be handed
    rows the engine did not produce.
    """
    try:
        info = validate_image_upload(content, filename)
    except ImageValidationError as exc:
        return {
            "headers": ["date", "demand_qty", "location"],
            "rows": [],
            "row_count": 0,
            "engine": ENGINE_NONE,
            "note": f"{exc}. Add the rows manually, or upload a valid image.",
            "quality": {"width": None, "height": None, "format": None},
            "extracted_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }

    extraction = extract_rows_with_engine(content, filename)
    return {
        "headers": ["date", "demand_qty", "location"],
        "rows": extraction.rows,
        "row_count": len(extraction.rows),
        # Travels with the rows so the review screen can word `confidence` for the
        # engine that actually produced it (see `extract_rows_with_engine`).
        "engine": extraction.engine,
        "note": _note_for(extraction),
        # Measured engine metadata, separate from each row's line-legibility
        # score: dimensions help explain a failed read, and mean_confidence is a
        # real Tesseract/vision-model score rather than a heuristic.
        "quality": {
            **extraction.quality,
            "width": info.width,
            "height": info.height,
            "format": info.format,
        },
        "extracted_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
