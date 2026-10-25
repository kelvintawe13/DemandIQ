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
import math
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
# Seven digits, so a count up to the platform ceiling (``normaliser.MAX_DEMAND_QTY``)
# is readable rather than silently dropped for being wider than the pattern.
_INT = re.compile(r"(?<!\d)(\d{1,7})(?!\d)")

# Tesseract frequently reads a printed/handwritten 7 as '/' when it ends a
# count token ("2/" for 27). Only the unambiguous single-digit-before-slash
# form is corrected; a date's "2/05" is left alone because a digit follows, and
# integers inside the date are not count candidates in the first place.
_TRAILING_SEVEN = re.compile(r"(?<!\d)(\d)[/|\\](?!\d)")

# What `parse_ocr_text` puts in a row's `confidence`, and what those numbers mean.
# They describe how legible the line was, not how likely the value is correct.
CONFIDENCE_UNAMBIGUOUS_LINE = 0.9
CONFIDENCE_AMBIGUOUS_LINE = 0.75

# Labels that name a number instead of being the day's demand count. A written
# log carries "Table 6" or "tips 3" beside the covers all the time, and taking
# the last integer on such a line imported the table number as the visitor
# count. Only words that unambiguously annotate a number are listed: "covers"
# and "visitors" are labels for the *count* and must stay eligible.
_ANNOTATION_LABELS = frozenset(
    {
        "table",
        "tables",
        "tip",
        "tips",
        "room",
        "rooms",
        "ref",
        "no",
        "nr",
        "pax",
        "note",
        "notes",
        "page",
        "pg",
        "staff",
        "order",
        "orders",
        "id",
    }
)

# The platform's own storage ceiling, mirrored from
# ``app.services.ingestion.normaliser.MAX_DEMAND_QTY`` so this module stays free
# of database imports. ``test_drift_ocr_jobs.py`` asserts the two agree.
MAX_PLAUSIBLE_DEMAND = 10_000_000

# Pre-processing is deliberately conservative: phone photos are usually large
# enough but skewed or low-contrast, while a tiny thumbnail is unreadable at any
# contrast. Upscaling below 800 px on the short edge and normalising contrast is
# enough to help Tesseract without inventing strokes in handwriting.
_UPSCALE_BELOW_EDGE = 800
_UPSCALE_TO_EDGE = 1600
_MAX_UPSCALE = 3.0

# Deskew search: a few degrees either way, measured on a small probe so the
# extra work does not double the OCR latency.
_DESKEW_PROBE_WIDTH = 240
_DESKEW_MAX_DEGREES = 6.0


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


_MIME_BY_FORMAT = {
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "webp": "image/webp",
}


def image_mime_type(detected_format: str | None) -> str:
    """Content type for a photo stored on an upload, from its sniffed format.

    Deliberately not the client's Content-Type: the type served back to the
    browser is decided by the bytes that were accepted, so a mislabelled upload
    cannot come back as something else.
    """
    return _MIME_BY_FORMAT.get((detected_format or "").lower(), "application/octet-stream")


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


@dataclass(frozen=True, slots=True)
class _Preprocess:
    """How the image handed to Tesseract relates to the upload.

    Tesseract's boxes are in the coordinates of the straightened, upscaled,
    padded copy it read, not of the photo the operator is looking at. The review
    screen draws its highlights on the photo, so those boxes have to be mapped
    back through every step this module applied.
    """

    scale: float
    angle: float
    border: int
    source_size: tuple[int, int]
    rotated_size: tuple[int, int]

    def box_to_source(
        self, box: tuple[int, int, int, int]
    ) -> tuple[float, float, float, float] | None:
        """One OCR box as ``(x, y, w, h)`` fractions of the original photo.

        Fractions rather than pixels, because the review screen sizes the photo
        to its container and a pixel offset from the OCR copy would not line up
        with it at any other size.

        The deskew step rotates the content about its centre and the padding is
        added afterwards, so undoing it is: drop the padding, rotate each corner
        back about the centre of the rotated content, then divide by the scale
        the upscale applied. Corners are transformed and re-bounded because a
        rotated rectangle is not axis-aligned; the highlight is an axis-aligned
        box around the same ink, which is what an overlay can draw.
        """
        width, height = self.source_size
        if width <= 0 or height <= 0:
            return None
        theta = math.radians(self.angle)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        cx = self.rotated_size[0] / 2
        cy = self.rotated_size[1] / 2
        left, top, w, h = box
        corners: list[tuple[float, float]] = []
        for x, y in ((left, top), (left + w, top), (left, top + h), (left + w, top + h)):
            dx = (x - self.border) - cx
            dy = (y - self.border) - cy
            # Inverse of PIL's rotate(angle, expand=True): its forward map is
            # (dx·cos + dy·sin, -dx·sin + dy·cos) about the rotated centre.
            corners.append(
                (
                    (dx * cos_t - dy * sin_t) / self.scale + width / 2,
                    (dx * sin_t + dy * cos_t) / self.scale + height / 2,
                )
            )
        xs = [corner[0] for corner in corners]
        ys = [corner[1] for corner in corners]
        return (
            round(max(0.0, min(xs)) / width, 4),
            round(max(0.0, min(ys)) / height, 4),
            round(min(width, max(xs) - min(xs)) / width, 4),
            round(min(height, max(ys) - min(ys)) / height, 4),
        )


def _prepare_image(content: bytes):  # type: ignore[no-untyped-def]
    """The image Tesseract should read for this upload (see :func:`_preprocess`)."""
    return _preprocess(content)[0]


def _preprocess(content: bytes) -> tuple[Any, _Preprocess]:
    """Decode, orient, upscale and normalise an image for Tesseract.

    EXIF rotation is applied first: a portrait phone photo stored landscape is
    the most common way OCR silently reads nothing. Contrast is stretched and a
    white border added, both of which help Tesseract's layout analysis; no
    thresholding is applied because binarising handwriting can erase the very
    strokes that distinguish a 7 from a 1.

    Returns the prepared image and the transform that maps its coordinates back
    to the upload, which is what lets a row be highlighted on the photo.
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

    source_size = image.size
    width, height = image.size
    scale = 1.0
    shortest = min(width, height)
    if shortest and shortest < _UPSCALE_BELOW_EDGE:
        factor = min(_UPSCALE_TO_EDGE / shortest, _MAX_UPSCALE)
        if factor > 1.05:
            resample = getattr(Image, "Resampling", Image).LANCZOS
            image = image.resize(
                (max(1, int(width * factor)), max(1, int(height * factor))),
                resample,
            )
            # Recorded as what was actually applied, not the requested factor:
            # the resize rounds to whole pixels (and to a minimum of 1).
            scale = image.size[0] / width if width else 1.0

    gray = ImageOps.grayscale(image)
    # Lighting first: a shadow crossing the page reads as a tilt to the deskew
    # search (it changes how dark pixels fall across rows), so flattening it
    # before measuring the angle is what keeps a well-lit page unrotated.
    gray = _flatten_illumination(gray)
    gray, angle = _deskew(gray)
    rotated_size = gray.size
    # A small quiet border keeps text away from the crop edge, where Tesseract's
    # layout analysis otherwise drops the first/last line of a tight photo.
    border = 16
    prepared = ImageOps.expand(gray, border=border, fill=255)
    return prepared, _Preprocess(
        scale=scale,
        angle=angle,
        border=border,
        source_size=source_size,
        rotated_size=rotated_size,
    )


def _row_projection_score(image):  # type: ignore[no-untyped-def]
    """How strongly the ink lines up in horizontal bands (higher is straighter)."""
    from PIL import ImageOps

    raw = ImageOps.invert(image).tobytes()
    width, height = image.size
    rows = [sum(raw[y * width : (y + 1) * width]) for y in range(height)]
    mean = sum(rows) / max(1, height)
    return sum((row - mean) ** 2 for row in rows)


def _deskew(gray):  # type: ignore[no-untyped-def]
    """Straighten a page photographed a few degrees off square, and report the angle.

    Tesseract's line finding wants a roughly horizontal baseline: a 4.5-degree
    tilt was enough to lose a line and misread another. The angle is chosen on a
    small probe by maximising the row projection (a straight page puts every
    line's ink on one band of rows), then applied once to the full image. Only
    small angles are considered — a 90-degree turn is a sideways photo, which
    EXIF and OSD handle differently, and rotating on a guess there is worse than
    leaving it alone.
    """
    from PIL import Image

    scale = _DESKEW_PROBE_WIDTH / max(gray.size)
    probe = (
        gray
        if scale >= 1
        else gray.resize((max(1, int(gray.width * scale)), max(1, int(gray.height * scale))))
    )
    zero_score = _row_projection_score(probe)
    best_angle, best_score = 0.0, zero_score
    angle = -_DESKEW_MAX_DEGREES
    while angle <= _DESKEW_MAX_DEGREES:
        rotated = probe.rotate(angle, resample=Image.BILINEAR, fillcolor=255)
        score = _row_projection_score(rotated)
        if score > best_score:
            best_angle, best_score = angle, score
        angle += 1.0
    # A page that is already square must not be rotated on noise: only a clear
    # improvement in line alignment is worth blurring the photo to achieve.
    if best_angle == 0.0 or best_score < zero_score * 1.01:
        return gray, 0.0
    logger.info("deskewing photo by %.0f degrees", best_angle)
    # The angle travels with the image: a box Tesseract reports is in rotated
    # coordinates, and mapping it back to the photo needs this exact value.
    return gray.rotate(best_angle, resample=Image.BICUBIC, expand=True, fillcolor=255), best_angle


def _flatten_illumination(gray):  # type: ignore[no-untyped-def]
    """Remove shadows and speckle, then stretch contrast.

    A phone photo of a notebook is rarely evenly lit: a shadow across the page
    turned half of a test page's lines into smudges Tesseract read none of.
    Subtracting a heavily blurred copy of the page (its lighting) leaves the
    ink. Neither invents strokes, and no thresholding is applied — binarising
    handwriting erases the very strokes that distinguish a 7 from a 1.

    A median filter was tried here and removed: it cleaned speckle the
    flattening already handled, and on hand-written lines it smoothed strokes
    into different digits (a measured 98 read back as 9, a 134 as 434).
    """
    from PIL import ImageChops, ImageFilter, ImageOps

    radius = max(12, min(gray.size) // 20)
    background = gray.filter(ImageFilter.GaussianBlur(radius))
    flattened = ImageChops.subtract(gray, background, scale=1.0, offset=255)
    return ImageOps.autocontrast(flattened, cutoff=1)


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


@dataclass(frozen=True, slots=True)
class _ParsedRow:
    """A row the parser accepted, with the line and spans it came from.

    The spans are what let the Tesseract path attach the engine's bounding boxes
    to the exact value that was chosen, rather than to the whole line.
    """

    line_index: int
    line_text: str
    date: datetime.date
    demand_qty: int
    confidence: float
    date_span: tuple[int, int]
    count_span: tuple[int, int]

    def as_row(self, row_index: int) -> dict[str, Any]:
        return {
            "row_index": row_index,
            "date": self.date.isoformat(),
            "demand_qty": self.demand_qty,
            "location": None,
            "notes": None,
            "confidence": self.confidence,
            # Where on the photo the value was read, as fractions of the upload.
            # ``None`` until an engine reports geometry (the local one does).
            "boxes": {"date": None, "demand_qty": None},
        }


def _preceding_word(line: str, start: int) -> str:
    """The word immediately before ``start``, lowercased with punctuation trimmed."""
    index = start - 1
    while index >= 0 and line[index].isspace():
        index -= 1
    end = index + 1
    while index >= 0 and (line[index].isalpha() or line[index] in ".#:\u2013-"):
        index -= 1
    return line[index + 1 : end].strip(".#:\u2013-").lower()


def _count_candidates(
    line: str, date_span: tuple[int, int]
) -> list[tuple[int, int, int, int, bool]]:
    """Every integer on the line that could be the demand count.

    Each entry is ``(value, distance_to_date, start, end, corrected)``, ordered
    nearest the date first. Integers inside the date are not candidates, and a
    number directly preceded by an annotation label ("Table 6", "tips 3") is
    not a count either — taking it was how a table number became a visitor
    count and got committed as one.
    """
    candidates: list[tuple[int, int, int, int, bool]] = []
    for match in _INT.finditer(line):
        start, end = match.span()
        if date_span[0] <= start < date_span[1]:
            continue
        if _preceding_word(line, start) in _ANNOTATION_LABELS:
            continue
        value = int(match.group(1))
        corrected = False
        # Tesseract frequently reads a terminal 7 as '/' when it ends a count
        # ("2/" is 27). Only the single-digit form is corrected, and only when
        # no further digit follows: "2/05" is a date fragment, not 27.
        if len(match.group(1)) == 1 and end < len(line) and line[end] in "/|\\":
            if end + 1 >= len(line) or not line[end + 1].isdigit():
                value = value * 10 + 7
                corrected = True
                end += 1
        distance = min(abs(start - date_span[1]), abs(date_span[0] - end))
        candidates.append((value, distance, start, end, corrected))
    candidates.sort(key=lambda candidate: (candidate[1], candidate[2]))
    return candidates


def _parse_lines(lines: list[str], *, day_first: bool) -> list[_ParsedRow]:
    """The parser proper, keeping the line and spans each row came from.

    ``parse_ocr_text`` returns plain dicts; the Tesseract path needs the spans as
    well, so it can attach the engine's boxes to the value that was chosen.
    """
    parsed: list[_ParsedRow] = []
    for line_index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        date, span = _find_date(line, day_first=day_first)
        if date is None or span is None:
            continue
        candidates = _count_candidates(line, span)
        if not candidates:
            continue
        demand_qty, _, count_start, count_end, corrected = candidates[0]
        # A readability signal, not a calibrated probability. One candidate means
        # the count was unambiguous; several means the nearest to the date was
        # chosen for the operator, which is a guess worth flagging. The remote
        # service supplies its own real score instead — `engine` on the preview
        # records which of the two this is.
        confidence = (
            CONFIDENCE_UNAMBIGUOUS_LINE
            if len(candidates) == 1 and not corrected
            else CONFIDENCE_AMBIGUOUS_LINE
        )
        parsed.append(
            _ParsedRow(
                line_index=line_index,
                line_text=line,
                date=date,
                demand_qty=demand_qty,
                confidence=confidence,
                date_span=span,
                count_span=(count_start, count_end),
            )
        )
    return parsed


def parse_ocr_text(text: str, *, day_first: bool | None = None) -> list[dict[str, Any]]:
    """Parse OCR text into candidate ``{date, demand_qty}`` rows.

    Each output line is expected to carry one date and one demand count,
    typically ``"12/01/2026   112"``. The count is the number nearest the date
    that is neither a date fragment nor the value of a label such as
    ``"Table 6"``, so a note beside the count no longer replaces it. Lines
    without both a date and a count are skipped rather than guessed at.

    A count of ``0`` is a real day (a restaurant that served nobody) and is kept;
    negative values are what the platform refuses.

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
        ``confidence``, provenance in ``notes``, and ``boxes`` holding the
        on-photo position of each value when the engine reported one.
    """
    if day_first is None:
        day_first = _infer_day_first(text)
    return [
        parsed.as_row(index)
        for index, parsed in enumerate(_parse_lines(text.splitlines(), day_first=day_first))
    ]


def _extract_via_remote(content: bytes, filename: str, service_url: str) -> list[dict[str, Any]]:
    """POST the image to a configured OCR/vision service and parse its rows."""
    resp = httpx.post(
        service_url,
        files={"file": (filename, content)},
        timeout=30.0,
    )
    resp.raise_for_status()
    payload = resp.json()
    # A service may answer either ``{"rows": [...]}`` or a bare JSON array. The
    # list case has to be checked *before* calling ``.get`` — the old one-liner
    # did it inside `get`'s default argument, which is only evaluated for a dict,
    # so an array body raised AttributeError and the rows were thrown away.
    if isinstance(payload, dict):
        raw_rows = payload.get("rows", [])
    elif isinstance(payload, list):
        raw_rows = payload
    else:
        raw_rows = []
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
        # Zero is a service day with no visitors, which the platform stores; only
        # a negative count is impossible. Dropping zero silently lost that day.
        if demand_qty < 0:
            continue
        rows.append(
            {
                "row_index": idx,
                "date": date.isoformat(),
                "demand_qty": demand_qty,
                "location": None,
                "notes": None,
                "confidence": float(item.get("confidence", 0.9)),
                # A remote service is not required to report geometry; when it
                # does not, the review screen simply draws no highlight.
                "boxes": None,
            }
        )
    return rows


def _page_segmentation_modes() -> list[int]:
    """The Tesseract page-segmentation modes to try, in order.

    ``6`` assumes a uniform block of text and is the best default for a written
    log. ``4`` and ``11`` cover multi-column and sparse layouts; running all
    three and keeping the best-scoring extraction is a cheap way to be robust to
    an unfamiliar page without pulling in a layout model. "Best" is decided by
    :func:`_extraction_score`, not by which mode returned the most rows.
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


@dataclass(frozen=True, slots=True)
class _Word:
    """One Tesseract word: where it sits in its line, and its box."""

    start: int  # offset into the line with its whitespace removed
    end: int
    box: tuple[int, int, int, int]


def _line_key(text: str) -> str:
    """A line's text with whitespace removed, used to match OCR passes up."""
    return "".join(text.split())


def _squeezed_span(line: str, span: tuple[int, int]) -> tuple[int, int]:
    """A character span in ``line``, re-expressed without the whitespace."""
    return (len(_line_key(line[: span[0]])), len(_line_key(line[: span[1]])))


def _read_word_data(  # type: ignore[no-untyped-def]
    image, language: str, psm: int, timeout: float
) -> tuple[float | None, dict[str, list[_Word]]]:
    """Tesseract's words for one mode: mean confidence, and boxes grouped by line.

    Word confidence is a real engine score, unlike the line-legibility value in
    each row; the preview reports it separately so the two are never conflated.

    Lines are keyed by their text with whitespace removed, so a row parsed from
    ``image_to_string`` can be matched to the geometry reported by
    ``image_to_data`` — the two calls word the same page slightly differently.
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
    grouped: dict[tuple[int, int, int], list[tuple[str, tuple[int, int, int, int]]]] = {}
    order: list[tuple[int, int, int]] = []
    for index, raw_text in enumerate(data.get("text", [])):
        try:
            score = float(data["conf"][index])
        except (KeyError, IndexError, TypeError, ValueError):
            score = -1.0
        if score >= 0:
            scores.append(score)
        word = (raw_text or "").strip()
        if not word:
            continue
        try:
            key = (
                int(data["block_num"][index]),
                int(data["par_num"][index]),
                int(data["line_num"][index]),
            )
            box = (
                int(data["left"][index]),
                int(data["top"][index]),
                int(data["width"][index]),
                int(data["height"][index]),
            )
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append((word, box))

    by_line: dict[str, list[_Word]] = {}
    for key in order:
        offset = 0
        words: list[_Word] = []
        for word, box in grouped[key]:
            words.append(_Word(start=offset, end=offset + len(word), box=box))
            offset += len(word)
        by_line.setdefault(_line_key(" ".join(word for word, _ in grouped[key])), words)

    mean_confidence = round(sum(scores) / len(scores) / 100.0, 4) if scores else None
    return mean_confidence, by_line


def _union_box(words: list[_Word], span: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """The smallest box covering every word that overlaps ``span``."""
    selected = [word for word in words if word.start < span[1] and word.end > span[0]]
    if not selected:
        return None
    left = min(word.box[0] for word in selected)
    top = min(word.box[1] for word in selected)
    right = max(word.box[0] + word.box[2] for word in selected)
    bottom = max(word.box[1] + word.box[3] for word in selected)
    return left, top, right - left, bottom - top


def _attach_boxes(
    parsed: list[_ParsedRow],
    rows: list[dict[str, Any]],
    words_by_line: dict[str, list[_Word]],
    prep: _Preprocess,
) -> None:
    """Record where each row's date and count were read, on the photo."""
    for parsed_row, row in zip(parsed, rows):
        words = words_by_line.get(_line_key(parsed_row.line_text))
        if not words:
            continue
        date_box = _union_box(words, _squeezed_span(parsed_row.line_text, parsed_row.date_span))
        count_box = _union_box(words, _squeezed_span(parsed_row.line_text, parsed_row.count_span))
        row["boxes"] = {
            "date": prep.box_to_source(date_box) if date_box else None,
            "demand_qty": prep.box_to_source(count_box) if count_box else None,
        }


def _extraction_score(
    rows: list[dict[str, Any]], mean_confidence: float | None
) -> tuple[int, int, int, float]:
    """How good one segmentation mode's extraction looks, best score last.

    Row count alone is not accuracy: a mode that splits every line in two can
    report more rows than the mode that read the page correctly, and committing
    its duplicates would be worse than missing a day. Distinct dates are the
    coverage that actually matters, duplicate dates are evidence of a misread
    split, and a count above the platform ceiling could not be stored at all.
    """
    dates = [str(row.get("date")) for row in rows]
    distinct = len(set(dates))
    duplicates = len(dates) - distinct
    plausible = sum(
        1 for row in rows if 0 <= int(row.get("demand_qty") or 0) <= MAX_PLAUSIBLE_DEMAND
    )
    return (distinct, -duplicates, plausible, mean_confidence or 0.0)


def _tesseract_extraction(content: bytes, language: str) -> ImageExtraction:
    """Run local Tesseract with preprocessing and several layout modes.

    Raises:
        ImportError: If ``pytesseract`` or Pillow is not installed.
        ImageValidationError: If the image cannot be decoded.
        RuntimeError: If every segmentation mode fails at the engine level.
    """
    import pytesseract

    image, prep = _preprocess(content)
    timeout = float(getattr(get_settings(), "ocr_timeout_seconds", 30.0))
    best: ImageExtraction | None = None
    best_score: tuple[int, int, int, float] | None = None
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

        parsed = _parse_lines(text.splitlines(), day_first=_infer_day_first(text))
        if not parsed:
            continue
        rows = [row.as_row(index) for index, row in enumerate(parsed)]
        try:
            mean_confidence, words_by_line = _read_word_data(image, language, psm, timeout)
        except Exception:
            mean_confidence, words_by_line = None, {}
        if words_by_line:
            _attach_boxes(parsed, rows, words_by_line, prep)

        score = _extraction_score(rows, mean_confidence)
        candidate = ImageExtraction(
            rows=rows,
            engine=ENGINE_TESSERACT,
            quality={
                "page_segmentation_mode": psm,
                "mean_confidence": mean_confidence,
                "rows": len(rows),
                "distinct_dates": score[0],
                "duplicate_dates": -score[1],
            },
        )
        if best is None or best_score is None or score > best_score:
            best, best_score = candidate, score

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


def _row_warnings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dates that were read more than once, and whether their counts disagree.

    Two rows for one service day cannot both be stored: the commit upserts on
    ``(location_id, date)``, so the later row silently replaces the earlier one
    in a list the operator was asked to check. Naming the dates here lets the
    review screen say so before the import rather than after it.
    """
    by_date: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        by_date.setdefault(str(row.get("date")), []).append(index)

    warnings: list[dict[str, Any]] = []
    for date in sorted(by_date):
        indexes = by_date[date]
        if len(indexes) < 2:
            continue
        quantities = [rows[index]["demand_qty"] for index in indexes]
        conflicting = len(set(quantities)) > 1
        counts = ", ".join(str(quantity) for quantity in quantities)
        warnings.append(
            {
                "code": "conflicting_date" if conflicting else "duplicate_date",
                "date": date,
                "row_indexes": indexes,
                "demand_qtys": quantities,
                "message": (
                    f"{date} was read {len(indexes)} times with different counts "
                    f"({counts}); only one row can be imported for that day"
                    if conflicting
                    else f"{date} was read {len(indexes)} times; only one row "
                    "can be imported for that day"
                ),
            }
        )
    return warnings


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
            "warnings": [],
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
        # Duplicate or conflicting dates, so the screen can raise them before the
        # operator commits rows that would overwrite each other.
        "warnings": _row_warnings(extraction.rows),
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
