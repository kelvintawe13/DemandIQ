"""Image ingestion — OCR extraction for handwritten/printed demand logs.

The adapter turns a photo of an operator's log into candidate
``{date, demand_qty}`` rows for human confirmation before commit
(``ARCHITECTURE.md`` §"Handwritten photo → OCR → human-confirmed"):

1. **Custom OCR service** — when ``OCR_SERVICE_URL`` is configured the image is
   POSTed there and the service returns JSON rows.
2. **Vision model** — when ``OCR_VISION_URL`` is configured the image is sent
   to an OpenAI-shaped chat-completions endpoint and its JSON rows are used.
   This is the path that can read *handwriting*: Tesseract recognises printed
   glyphs, and on a measured hand-written page it recovered nine of twenty-one
   days at best while the rest were never recognised by any layout mode.
3. **Local Tesseract** — used automatically when ``pytesseract`` and
   ``Pillow`` are installed (``pip install 'demandiq-backend[ocr]'``), and as
   the fallback when a configured service fails or reads nothing.
4. **Not configured** — returns no rows plus an explicit note telling the
   operator to type them in.

The adapter never invents rows. A fabricated preview that an operator might
commit without reading would silently corrupt the demand history the models
are trained on, so an honest empty preview is the correct failure mode.
"""

from __future__ import annotations

import datetime
import io
import json
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
# count token ("2/" for 27). Only the unambiguous single-digit-before-glyph form
# is corrected; a date's "2/05" is left alone because a digit follows, and
# integers inside the date are not count candidates in the first place.
#
# The same end-of-token 7 also comes back as an ``F`` when the stroke's top bar
# is read as a vertical: on a measured page ``27`` was transcribed as ``2F``,
# which parsed as 2 and staged a 27-visitor day as 2. Only the trailing position
# is corrected — an ``F`` between digits is not a 7, and a count is the last
# number on its line.
_TRAILING_SEVEN_GLYPHS = "/|\\Ff"

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

# How much of a word has to sit inside a row's vertical band for the two to
# belong together. Handwriting is ragged, so a word rarely lines up with its row
# exactly; but two rows a line apart do not overlap at all, which is what stops
# this from merging a date with the next day's count.
_ROW_OVERLAP = 0.5

# Rows drift downwards to the right on a photographed page, and a degree or two
# of residual tilt is enough to put the count column a line-height below its date.
# The slope is searched over this range and only used when it clearly sharpens the
# bands; ±0.15 is about 8.5 degrees.
_ROW_SLOPE_LIMIT = 0.15
_ROW_SLOPE_STEP = 0.01

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

    Tesseract's 
      are in the coordinates of the straightened, upscaled,
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


def _default_year(reporting_year: int | None) -> int:
    """The year to assume when a date is written without one.

    A handwritten log usually says "12 Oct" and leaves the year to context. Using
    today's year for that imports last year's page as this year's demand, and
    nothing downstream can tell the difference — so the operator states the
    reporting year when they upload, and only a caller that states none falls
    back to the current year.
    """
    return reporting_year or datetime.datetime.now(datetime.UTC).year


def _glyph_digits(raw: str) -> int | None:
    """A day written with letter look-alikes, as an int — or ``None`` if it is not one.

    Tesseract reads a handwritten 1 as ``l``, ``I``, ``|`` or ``L``, which is why a
    month-first date's day is matched loosely. Upper case matters: the pattern is
    case-insensitive, so an ``L`` reaches here, and substituting only the lower-case
    forms raised ``ValueError``. That exception escaped the whole page — a single
    unreadable glyph in ``Oct L`` returned *no* rows for a photo of twenty-one.
    Returning ``None`` instead lets the parser try its other date forms.
    """
    try:
        return int(re.sub(r"[lLI|]", "1", raw))
    except ValueError:
        return None


def _find_date(
    line: str, *, day_first: bool = True, reporting_year: int | None = None
) -> tuple[datetime.date | None, tuple[int, int] | None]:
    """Return the first recognisable date on a line and its character span.

    ``reporting_year`` is used only for dates the sheet leaves yearless.
    """
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
            year = int(year_raw) if year_raw else _default_year(reporting_year)
            date = _safe_date(year, month or 0, match.group(1))
        else:  # month first, e.g. "Oct 1" or "October 12, 2026"
            month = _MONTHS.get(match.group(1).lower()[:3])
            # Tesseract commonly reads a handwritten/printed 1 as l, I or |.
            # That turns "Oct 1" into "Oct l", which is still a date to a human.
            day = _glyph_digits(match.group(2))
            if day is None:
                continue
            year_raw = match.group(3)
            year = int(year_raw) if year_raw else _default_year(reporting_year)
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
        if len(match.group(1)) == 1 and end < len(line) and line[end] in _TRAILING_SEVEN_GLYPHS:
            if end + 1 >= len(line) or not line[end + 1].isdigit():
                value = value * 10 + 7
                corrected = True
                end += 1
        distance = min(abs(start - date_span[1]), abs(date_span[0] - end))
        candidates.append((value, distance, start, end, corrected))
    candidates.sort(key=lambda candidate: (candidate[1], candidate[2]))
    return candidates


def _parse_lines(
    lines: list[str], *, day_first: bool, reporting_year: int | None = None
) -> list[_ParsedRow]:
    """The parser proper, keeping the line and spans each row came from.

    ``parse_ocr_text`` returns plain dicts; the Tesseract path needs the spans as
    well, so it can attach the engine's boxes to the value that was chosen.
    """
    parsed: list[_ParsedRow] = []
    for line_index, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        date, span = _find_date(line, day_first=day_first, reporting_year=reporting_year)
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


def parse_ocr_text(
    text: str, *, day_first: bool | None = None, reporting_year: int | None = None
) -> list[dict[str, Any]]:
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
        reporting_year: Year to use for a date written without one (``Oct 1``,
            ``3 Jan``). ``None`` uses the current year, which is right for a log
            kept this year and wrong for an archive page — callers that know
            which reporting period the sheet covers should pass it.

    Returns:
        Candidate rows with ``row_index``, ISO ``date``, ``demand_qty``,
        ``confidence``, provenance in ``notes``, and ``boxes`` holding the
        on-photo position of each value when the engine reported one.
    """
    if day_first is None:
        day_first = _infer_day_first(text)
    return [
        parsed.as_row(index)
        for index, parsed in enumerate(
            _parse_lines(text.splitlines(), day_first=day_first, reporting_year=reporting_year)
        )
    ]


def _rows_from_items(items: Any) -> list[dict[str, Any]]:
    """Normalise a service's JSON rows into the preview's row shape.

    Shared by the custom OCR endpoint and the vision model, so a provider's
    quirks (``quantity``/``count`` for the value, a string for the date) are
    handled once instead of per provider. A row missing a date or a count is
    dropped rather than defaulted: an invented row would be committed as real
    demand.
    """
    rows: list[dict[str, Any]] = []
    if not isinstance(items, (list, tuple)):
        return rows
    for item in items:
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
                "row_index": len(rows),
                "date": date.isoformat(),
                "demand_qty": demand_qty,
                "location": None,
                "notes": None,
                "confidence": float(item.get("confidence", 0.9)),
                # A service is not required to report geometry; when it does
                # not, the review screen simply draws no highlight.
                "boxes": None,
            }
        )
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
    # A service may answer either ``{"rows": [...]}`` or a bare JSON array. The
    # list case has to be checked *before* calling ``.get`` — the old one-liner
    # did it inside `get`'s default argument, which is only evaluated for a dict,
    # so an array body raised AttributeError and the rows were thrown away.
    if isinstance(payload, dict):
        return _rows_from_items(payload.get("rows", []))
    return _rows_from_items(payload)


# The vision model is asked for this shape, and nothing else. A photo of a
# handwritten log defeats Tesseract because glyph shapes vary between hands; a
# vision model reads the page the way a person does, including which count sits
# beside which date — which is the failure the measured page showed most of.
_VISION_PROMPT = """You read a photograph of a handwritten visitor log from a restaurant or shop.

Answer with ONE JSON object and nothing else — no prose, no code fences:

{"rows": [{"date": "YYYY-MM-DD", "demand_qty": 0, "confidence": 0.0}]}

Rules:
- One row per service day actually written on the page.
- demand_qty is that day's visitor/cover count. It is not a table number, a
  page number, a running total, or any label.
- Ignore annotations ("busy", "holiday?", "check", "rain", arrows, ticks) and
  ignore struck-through or scribbled-over numbers: a correction replaces what
  was crossed out, so use only the number that stands.
- A date without a year belongs to %s.
- Omit rows you cannot read rather than guessing. Do not invent rows, and do
  not carry a count over from a neighbouring line.
- confidence is your own 0-1 estimate that the date and count are both right.
"""


def _strip_code_fence(text: str) -> str:
    """A model's JSON reply with an accidental ```json fence removed."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped[3:]
    if body[:4].lower() == "json":
        body = body[4:]
    return body.rsplit("```", 1)[0].strip()


def _extract_via_vision(
    content: bytes,
    *,
    url: str,
    api_key: str | None,
    model: str,
    timeout: float,
    reporting_year: int | None = None,
) -> list[dict[str, Any]]:
    """Read the photo with a vision model over the OpenAI chat-completions shape.

    That shape is the widest common denominator for image input: a hosted
    provider, or a local Ollama server running a vision model, both answer it,
    so handwriting support does not force either a cloud account or a vendor
    SDK. The reply is asked for as JSON and parsed defensively — a model that
    wraps its JSON in prose is a formatting problem, not a failed read.

    Raises:
        RuntimeError: If the endpoint replies with something that is not a row
            list, so the caller can fall back and say why.
    """
    import base64

    detected = _sniff_image_format(content)
    mime = image_mime_type(detected)
    if mime == "application/octet-stream":  # sniffable but outside the MIME map
        mime = "image/jpeg"
    data_url = f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"
    year = _default_year(reporting_year)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response = httpx.post(
        url,
        headers=headers,
        json={
            "model": model,
            "temperature": 0,
            "max_tokens": 2000,
            "messages": [
                {"role": "system", "content": _VISION_PROMPT % year},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Read this page."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    try:
        reply = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"vision reply had no message content ({exc})") from exc
    if isinstance(reply, list):  # some providers return content parts
        reply = "".join(part.get("text", "") for part in reply if isinstance(part, dict))
    try:
        data = json.loads(_strip_code_fence(str(reply)))
    except ValueError as exc:
        raise RuntimeError(f"vision reply was not JSON ({exc})") from exc
    items = data.get("rows", []) if isinstance(data, dict) else data
    return _rows_from_items(items)


def _page_segmentation_modes() -> list[int]:
    """The Tesseract page-segmentation modes to try, in order.

    ``6`` assumes a uniform block of text and is the best default for a written
    log. ``4`` and ``11`` cover multi-column and sparse layouts; running all
    three and merging their rows is a cheap way to be robust to an unfamiliar
    page without pulling in a layout model. Merging rather than ranking matters:
    two modes on one hand-written page routinely read different halves of it, and
    :func:`_extraction_score` is only used to choose between the two
    reconstructions of a single mode.
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
    """One Tesseract word: its text, where it sits in its line, and its box."""

    text: str
    start: int  # offset into the line with its whitespace removed
    end: int
    box: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class _WordData:
    """One segmentation mode's word-level output."""

    mean_confidence: float | None
    by_line: dict[str, list[_Word]]
    # Every word in engine order. The line-based parser uses `by_line`; the
    # layout reconstruction regroups these by position, because a sheet read
    # column-by-column has dates and counts in different engine lines.
    words: tuple[_Word, ...]


def _line_key(text: str) -> str:
    """A line's text with whitespace removed, used to match OCR passes up."""
    return "".join(text.split())


def _squeezed_span(line: str, span: tuple[int, int]) -> tuple[int, int]:
    """A character span in ``line``, re-expressed without the whitespace."""
    return (len(_line_key(line[: span[0]])), len(_line_key(line[: span[1]])))


def _read_word_data(  # type: ignore[no-untyped-def]
    image, language: str, psm: int, timeout: float
) -> _WordData:
    """Tesseract's words for one mode: mean confidence, and the words themselves.

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
    flat: list[_Word] = []
    for key in order:
        offset = 0
        words: list[_Word] = []
        for word, box in grouped[key]:
            entry = _Word(text=word, start=offset, end=offset + len(word), box=box)
            words.append(entry)
            flat.append(entry)
            offset += len(word)
        by_line.setdefault(_line_key(" ".join(word for word, _ in grouped[key])), words)

    mean_confidence = round(sum(scores) / len(scores) / 100.0, 4) if scores else None
    return _WordData(mean_confidence=mean_confidence, by_line=by_line, words=tuple(flat))


def _row_alignment_score(centres: list[tuple[float, float]], slope: float, x0: float) -> float:
    """How sharply word centres collapse into horizontal bands at one slope.

    Sum of squared band sizes: words that share a row pile into the same 2-pixel
    band and raise the score, which is the alignment being looked for.
    """
    bins: dict[int, int] = {}
    for x, y in centres:
        key = int(round((y - slope * (x - x0)) / 2.0))
        bins[key] = bins.get(key, 0) + 1
    return float(sum(count * count for count in bins.values()))


def _row_slope(words: list[_Word] | tuple[_Word, ...]) -> float:
    """How far the rows drift down to the right, measured from the words.

    Deskew straightens the page, but handwriting wanders and a photo can still be
    a degree off. On a wide sheet that is enough for the count column to sit a
    line below its date, so a small range of slopes is searched and the one that
    makes the bands sharpest wins — unless plain horizontal is nearly as good, in
    which case nothing is tilted on noise.
    """
    if len(words) < 4:
        return 0.0
    centres = [(word.box[0] + word.box[2] / 2, word.box[1] + word.box[3] / 2) for word in words]
    x0 = min(x for x, _ in centres)
    steps = round(_ROW_SLOPE_LIMIT / _ROW_SLOPE_STEP)
    scored = [
        (_row_alignment_score(centres, step * _ROW_SLOPE_STEP, x0), step)
        for step in range(-steps, steps + 1)
    ]
    horizontal = next(score for score, step in scored if step == 0)
    best = max(score for score, _ in scored)
    if best < horizontal * 1.02:
        return 0.0
    # Several slopes can read a page equally well — a small tilt that puts every
    # count beside its date, and a large one that pairs the date with the next
    # row's count instead. Both collapse words into the same number of bands, so
    # among the near-best slopes the smallest wins: the drift being corrected is
    # small, and a large angle is only ever pairing the wrong rows.
    near_best = [(abs(step), step) for score, step in scored if score >= best * 0.98]
    return min(near_best)[1] * _ROW_SLOPE_STEP


def _visual_rows(words: list[_Word] | tuple[_Word, ...]) -> list[list[_Word]]:
    """Group words into the rows a human sees, ignoring Tesseract's own lines.

    A two-column sheet is often read column-by-column — every date in one engine
    line, every count in another — so a parser that trusts the engine's lines
    finds no line carrying both fields and imports nothing. Grouping by vertical
    position and reading each band left to right puts the date and its count back
    on one row, which is the association the operator would make.

    Two things make that reliable on a real page. The bands follow the page's own
    drift (``_row_slope``), so a slanted line does not split its left and right
    halves; and a word joins a band only when it sits mostly inside it, so rows
    written close together stay separate — a merged row pairs a date with the
    wrong day's count, which is worse than missing a row.
    """
    if not words:
        return []
    slope = _row_slope(words)
    x0 = min(word.box[0] for word in words)

    def top(word: _Word) -> float:
        # Remove the drift: a word further right is measured as if its row had
        # been written on a horizontal baseline.
        return word.box[1] - slope * (word.box[0] + word.box[2] / 2 - x0)

    rows: list[list[_Word]] = []
    bands: list[tuple[float, float]] = []
    for word in sorted(words, key=top):
        word_top = top(word)
        word_bottom = word_top + word.box[3]
        target: int | None = None
        # Sorted by top, so only the most recent bands can still overlap.
        for index in range(len(rows) - 1, max(-1, len(rows) - 4), -1):
            band_top, band_bottom = bands[index]
            overlap = min(word_bottom, band_bottom) - max(word_top, band_top)
            if word.box[3] and overlap / word.box[3] >= _ROW_OVERLAP:
                target = index
                break
        if target is None:
            rows.append([word])
            bands.append((word_top, word_bottom))
            continue
        rows[target].append(word)
        band_top, band_bottom = bands[target]
        bands[target] = (min(band_top, word_top), max(band_bottom, word_bottom))
    return [sorted(row, key=lambda word: word.box[0]) for row in rows]


def _row_text(row: list[_Word]) -> tuple[str, list[_Word]]:
    """A visual row's text, with word offsets rebuilt for that text."""
    parts: list[str] = []
    words: list[_Word] = []
    offset = 0
    for word in row:
        words.append(_Word(text=word.text, start=offset, end=offset + len(word.text), box=word.box))
        parts.append(word.text)
        offset += len(word.text)
    return " ".join(parts), words


def _cells_extraction(
    words: list[_Word] | tuple[_Word, ...],
    prep: _Preprocess,
    *,
    day_first: bool,
    reporting_year: int | None = None,
) -> list[dict[str, Any]]:
    """Read the sheet as rows of cells rather than as engine lines.

    Each visual row is parsed with the same rules as a line (one date, one count)
    but its words are known individually, so the value's box comes straight from
    the words that produced it instead of being matched back by text.
    """
    rows: list[dict[str, Any]] = []
    for visual_row in _visual_rows(words):
        text, row_words = _row_text(visual_row)
        parsed = _parse_lines([text], day_first=day_first, reporting_year=reporting_year)
        if not parsed:
            continue
        row_dicts = [entry.as_row(index) for index, entry in enumerate(parsed)]
        _attach_boxes(parsed, row_dicts, {_line_key(text): row_words}, prep)
        rows.extend(row_dicts)
    for index, row in enumerate(rows):
        row["row_index"] = index
    return rows


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


@dataclass(frozen=True, slots=True)
class _Read:
    """One recogniser's opinion of the page: a layout mode on one image variant."""

    rows: list[dict[str, Any]]
    source: str  # "lines" (the engine's own lines) or "cells" (grouped by position)
    variant: str  # "photo" (the prepared page) or "binarised" (a thresholded copy)
    psm: int
    mean_confidence: float | None


def _merge_reads(reads: list[_Read]) -> tuple[list[dict[str, Any]], int]:
    """One row per service day, chosen by how many independent reads agree.

    Ranking reads and keeping the winner was losing rows that had already been
    read. On a measured page of twenty-one hand-written days, block mode
    recovered days 1-3 and 8 while sparse mode recovered days 4, 5 and 12-15 —
    nearly disjoint sets, and only one of them survived — so merging is the
    extraction, not a tie-break.

    Reads are compared by date because that is the key the import commits on:
    two reads of one day are two opinions about one record, not two records.
    The value most reads agree on wins, and an outright tie goes to the value
    the more confident reads produced, since a clean read of a blurred page is
    stronger evidence than a strained one. A row keeps the boxes of the read
    that produced it, so the review screen highlights the ink it came from.

    Returns the merged rows and how many dates the reads disagreed on — the
    operator is asked to check every row anyway, and a date the recognisers
    argued about is the one worth checking first.

    Each merged row carries that doubt itself: ``readers`` is how many reads
    produced a row for that date at all, and ``agreement`` is the share of those
    reads that produced the value kept (1.0 = every read that found the day
    agreed). A row's ``confidence`` cannot say this — it describes how legible
    that read's line was — so evidence about the record is reported separately
    rather than folded into a number that already has a meaning.
    """
    votes: dict[str, list[tuple[dict[str, Any], _Read]]] = {}
    for read in reads:
        already_voted: set[str] = set()
        for row in read.rows:
            date = str(row.get("date"))
            if not date or date in already_voted:
                # One read gets one vote per date, however many times it split it.
                continue
            already_voted.add(date)
            votes.setdefault(date, []).append((row, read))

    merged: list[dict[str, Any]] = []
    disagreements = 0
    for date in sorted(votes):
        by_value: dict[int, list[tuple[dict[str, Any], _Read]]] = {}
        for row, read in votes[date]:
            by_value.setdefault(int(row.get("demand_qty") or 0), []).append((row, read))
        if len(by_value) > 1:
            disagreements += 1
        # `max` keeps the first of equal candidates, so the read order above is
        # the final tie-break: deterministic rather than arbitrary.
        _, group = max(
            by_value.items(),
            key=lambda item: (
                len(item[1]),
                sum(read.mean_confidence or 0.0 for _, read in item[1]),
            ),
        )
        row = dict(group[0][0])
        row["row_index"] = len(merged)
        row["readers"] = len(votes[date])
        row["agreement"] = round(len(group) / len(votes[date]), 2)
        merged.append(row)
    return merged, disagreements


def _binarise(gray: Any) -> Any:
    """A thresholded copy of the prepared page, read as a second opinion.

    The prepared image deliberately keeps mid-grey strokes — binarising a
    hand-written 7 can flatten it into a 1 (see :func:`_flatten_illumination`).
    On a creased or stained photo that same softness lets a shadow merge with
    ink, and a hard threshold at Otsu's cut is a genuinely different view of the
    page: on a measured photo it read nine days where the grey copy read seven.

    Otsu's cut maximises the variance between the dark and light classes, so it
    follows the photo's own exposure instead of a fixed level that would blank a
    dim page. Geometry is untouched, so a box still maps onto the upload.
    """
    histogram = gray.histogram()
    total = sum(histogram)
    if not total:
        return gray
    sum_all = sum(level * count for level, count in enumerate(histogram))
    sum_dark = weight_dark = 0
    best_variance, threshold = -1.0, 128
    for level, count in enumerate(histogram):
        weight_dark += count
        if not weight_dark:
            continue
        weight_light = total - weight_dark
        if not weight_light:
            break
        sum_dark += level * count
        mean_dark = sum_dark / weight_dark
        mean_light = (sum_all - sum_dark) / weight_light
        variance = weight_dark * weight_light * (mean_dark - mean_light) ** 2
        if variance > best_variance:
            best_variance, threshold = variance, level
    return gray.point(lambda value: 255 if value > threshold else 0, mode="L")


def _tesseract_extraction(
    content: bytes, language: str, *, reporting_year: int | None = None
) -> ImageExtraction:
    """Run local Tesseract with preprocessing and several layout modes.

    ``reporting_year`` is used for dates the sheet leaves yearless.

    Raises:
        ImportError: If ``pytesseract`` or Pillow is not installed.
        ImageValidationError: If the image cannot be decoded.
        RuntimeError: If every segmentation mode fails at the engine level.
    """
    import pytesseract

    image, prep = _preprocess(content)
    timeout = float(getattr(get_settings(), "ocr_timeout_seconds", 30.0))
    reads: list[_Read] = []
    failures: list[str] = []
    binarised = None

    for psm in _page_segmentation_modes():
        for variant in ("photo", "binarised"):
            # The thresholded copy is only worth reading where this mode already
            # found something on the photo: a mode that read nothing has no
            # rows to gain, and the extra pass costs real time.
            if variant == "binarised" and not any(read.psm == psm for read in reads):
                continue
            if variant == "binarised":
                binarised = binarised if binarised is not None else _binarise(image)
            page = image if variant == "photo" else binarised

            try:
                text = pytesseract.image_to_string(
                    page, lang=language, config=f"--psm {psm}", timeout=timeout
                )
            except TypeError:  # pytesseract < 0.3.10 has no timeout parameter
                text = pytesseract.image_to_string(page, lang=language, config=f"--psm {psm}")
            except Exception as exc:  # a failed pass must not discard the others
                failures.append(f"psm {psm} ({variant}): {exc}")
                continue

            day_first = _infer_day_first(text)
            parsed = _parse_lines(
                text.splitlines(), day_first=day_first, reporting_year=reporting_year
            )
            line_rows = [row.as_row(index) for index, row in enumerate(parsed)]
            try:
                word_data: _WordData | None = _read_word_data(page, language, psm, timeout)
            except Exception:  # geometry is an aid, not a requirement
                word_data = None

            mean_confidence = word_data.mean_confidence if word_data else None
            rows, row_source = line_rows, "lines"
            if word_data is not None:
                if line_rows:
                    _attach_boxes(parsed, line_rows, word_data.by_line, prep)
                # A sheet read column-by-column has every date in one engine line
                # and every count in another, so the line parser sees two halves
                # it cannot join. Re-grouping by position recovers those pairs; it
                # is only taken when it explains more of the page than the lines.
                cell_rows = _cells_extraction(
                    word_data.words, prep, day_first=day_first, reporting_year=reporting_year
                )
                if _extraction_score(cell_rows, mean_confidence) > _extraction_score(
                    line_rows, mean_confidence
                ):
                    rows, row_source = cell_rows, "cells"

            if not rows:
                continue
            reads.append(
                _Read(
                    rows=rows,
                    source=row_source,
                    variant=variant,
                    psm=psm,
                    mean_confidence=mean_confidence,
                )
            )

    if not reads:
        # Nothing was read. Raise only when the engine itself failed, so the
        # caller can distinguish "no engine" from "no readable rows".
        if failures:
            raise RuntimeError("; ".join(failures))
        return ImageExtraction(
            rows=[],
            engine=ENGINE_TESSERACT,
            detail="No readable rows were found in this photo.",
            quality={"page_segmentation_modes": _page_segmentation_modes(), "rows": 0},
        )

    rows, disagreements = _merge_reads(reads)
    anchor = max(reads, key=lambda read: (len(read.rows), read.mean_confidence or 0.0))
    return ImageExtraction(
        rows=rows,
        engine=ENGINE_TESSERACT,
        quality={
            # The mode and reconstruction that explained most of the page. Rows
            # may come from other reads too — `reads` and `variants` say how many
            # opinions were merged, and `read_disagreements` how many days they
            # argued over.
            "page_segmentation_mode": anchor.psm,
            "row_source": anchor.source,
            "mean_confidence": anchor.mean_confidence,
            "rows": len(rows),
            "distinct_dates": len(rows),
            "duplicate_dates": 0,
            "reads": len(reads),
            "variants": sorted({read.variant for read in reads}),
            "read_disagreements": disagreements,
        },
    )


def _extract_via_tesseract(
    content: bytes, language: str, *, reporting_year: int | None = None
) -> list[dict[str, Any]]:
    """The extracted rows alone, kept for callers that do not need quality."""
    return _tesseract_extraction(content, language, reporting_year=reporting_year).rows


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


def extract_rows_with_engine(
    content: bytes, filename: str, *, reporting_year: int | None = None
) -> ImageExtraction:
    """Extract candidate demand rows from an image, using the best engine.

    Tries the configured remote service, then a configured vision model, then
    local Tesseract, and otherwise returns no rows (never fabricated ones).
    Engine failures are logged and degrade to an empty extraction so the
    operator can still type rows in.

    The order is deliberate: a photo of handwriting is the case the local engine
    cannot read, so any service that can is preferred, and Tesseract remains the
    free offline fallback for a printed sheet.

    ``reporting_year`` is applied to dates the sheet writes without a year. It
    only affects the local parser: a remote service returns its own dates, read
    with its own date model, and is trusted as-is.

    The reported engine matters to the caller: a remote vision service returns a
    *model* confidence, while the local parser's ``confidence`` is a readability
    signal — how many numbers were left on the line after the date, i.e. whether
    picking the demand count was unambiguous. Presenting the second as a
    percentage would dress a heuristic up as a probability, so the engine travels
    with the rows and the screen labels each accordingly.
    """
    settings = get_settings()
    service_url = getattr(settings, "ocr_service_url", None)
    vision_url = getattr(settings, "ocr_vision_url", None)
    # Whichever configured service failed, so a fallback that also comes back
    # empty can say why instead of reporting a bare "no rows".
    service_failure: str | None = None

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
                    "provider": "custom",
                },
            )
        except Exception as exc:
            logger.warning("remote OCR service failed for %s", filename, exc_info=True)
            service_failure = str(exc) or exc.__class__.__name__

    if vision_url:
        try:
            vision_rows = _extract_via_vision(
                content,
                url=vision_url,
                api_key=getattr(settings, "ocr_vision_api_key", None),
                model=getattr(settings, "ocr_vision_model", "gpt-4o-mini"),
                timeout=float(getattr(settings, "ocr_vision_timeout_seconds", 90.0)),
                reporting_year=reporting_year,
            )
            # A model that answers with nothing is a failed read, not an empty
            # page: fall through to the local engine and let the highest-recall
            # answer win, rather than reporting zero rows on the strength of one
            # refusal.
            if vision_rows:
                logger.info("vision OCR extracted %s rows from %s", len(vision_rows), filename)
                scored = [
                    float(r["confidence"]) for r in vision_rows if r.get("confidence") is not None
                ]
                return ImageExtraction(
                    rows=vision_rows,
                    engine=ENGINE_REMOTE,
                    quality={
                        "mean_confidence": round(sum(scored) / len(scored), 4) if scored else None,
                        "rows": len(vision_rows),
                        "provider": "vision",
                        "model": getattr(settings, "ocr_vision_model", None),
                    },
                )
            logger.info("vision OCR read no rows from %s; trying the local engine", filename)
        except Exception as exc:
            logger.warning("vision OCR failed for %s", filename, exc_info=True)
            service_failure = str(exc) or exc.__class__.__name__

    try:
        extraction = _tesseract_extraction(
            content, getattr(settings, "ocr_language", "eng"), reporting_year=reporting_year
        )
        logger.info("tesseract OCR extracted %s rows from %s", len(extraction.rows), filename)
        return extraction
    except ImportError:
        logger.info(
            "OCR engine not installed. No rows extracted from %s "
            "(install 'demandiq-backend[ocr]', or set OCR_SERVICE_URL / OCR_VISION_URL)",
            filename,
        )
        if service_failure is not None:
            return ImageExtraction(
                rows=[],
                engine=ENGINE_NONE,
                detail=f"The OCR service could not be reached ({service_failure}) and no local engine is installed.",
            )
        return ImageExtraction(rows=[], engine=ENGINE_NONE, detail="No OCR engine is configured.")
    except Exception as exc:
        logger.warning("local OCR failed for %s", filename, exc_info=True)
        detail = f"Local OCR failed ({str(exc) or exc.__class__.__name__})."
        if service_failure is not None:
            detail = (
                f"The OCR service could not be reached ({service_failure}) and "
                f"{detail[0].lower()}{detail[1:]}"
            )
        return ImageExtraction(rows=[], engine=ENGINE_NONE, detail=detail)


def extract_rows_from_image(
    content: bytes, filename: str, *, reporting_year: int | None = None
) -> list[dict[str, Any]]:
    """The candidate rows alone, for callers that do not need the engine."""
    return extract_rows_with_engine(content, filename, reporting_year=reporting_year).rows


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


def preview_for_image(
    content: bytes, filename: str, *, reporting_year: int | None = None
) -> dict[str, Any]:
    """Build the preview payload stored on ``ingest_uploads.preview`` for images.

    Invalid bytes still produce a well-formed preview (never an exception): the
    upload route rejects them with a 400, while a direct caller gets the same
    honest empty result with the reason. The review screen must never be handed
    rows the engine did not produce.

    ``reporting_year`` is the operator's answer to "which year is this page?" for
    dates written without one. It is stored with the preview so a retry reads the
    page under the same assumption instead of silently switching to the current
    year.
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
            "reporting_year": reporting_year,
            "quality": {"width": None, "height": None, "format": None},
            "extracted_at": datetime.datetime.now(datetime.UTC).isoformat(),
        }

    extraction = extract_rows_with_engine(content, filename, reporting_year=reporting_year)
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
        "reporting_year": reporting_year,
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
