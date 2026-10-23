"""File ingestion helpers — delimiter sniffing, encoding, date parsing, column mapping.

Supports CSV (any delimiter) and XLSX. All parsing is done in memory for the
staging preview; the commit step reuses :func:`commit_demand` row-by-row so that
the normaliser's invariants cannot be bypassed.
"""

from __future__ import annotations

import csv
import datetime
import io
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Allowed column-map keys and their canonical targets.
CANONICAL_FIELDS: frozenset[str] = frozenset({"date", "demand_qty", "location", "notes"})

# Ordered: the day-first reading comes before the month-first one, so an
# ambiguous `03/12/2026` is read the way the rest of the pipeline reads it
# (day first). A value only unreadable in that order falls through to the next
# format, which is what makes `12/31/2026` still parse.
DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%Y%m%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%d.%m.%Y",
    "%d/%m/%y",
    "%m/%d/%y",
    "%d-%m-%y",
    "%y-%m-%d",
    # Written dates. Logs are hand-written as often as printed, and "Dec 3"
    # is the most common way to write a day on paper.
    "%d %b %Y",
    "%d-%b-%Y",
    "%d %B %Y",
    "%d-%B-%Y",
    "%b %d %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%B %d %Y",
    "%d %b",
    "%b %d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
)

# The words real logs, exports and notebooks use for the same four columns.
# Matching is on a normalised header (accents and punctuation removed, units in
# parentheses dropped) and on whole words, so "Date" and "Service date" both
# reach `date` while "update" does not.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "day", "service date", "business date", "period", "datum", "fecha"),
    "demand_qty": (
        "demand",
        "visitors",
        "visitor",
        # "Visits" is the word on the page in front of most operators, and its
        # absence had a consequence beyond a missing suggestion: a table headed
        # `date,visits` named only ONE canonical field, so `detect_header_row`
        # found no header at all, fell back to row 0, and read the restaurant's
        # title line as the column name — every row then collapsed into one
        # column. Found by driving a real log through the upload route.
        "visits",
        "visit",
        "no of visits",
        "covers",
        "cover",
        "guests",
        "guest",
        "customers",
        "customer",
        "footfall",
        "traffic",
        "count",
        "quantity",
        "qty",
        "pax",
        "sales",
        "orders",
        "entries",
        "attendance",
        "headcount",
    ),
    "location": (
        "location",
        "site",
        "store",
        "branch",
        "outlet",
        "venue",
        "restaurant",
        "shop",
        "unit",
    ),
    "notes": ("notes", "note", "comment", "comments", "remark", "remarks", "memo", "description"),
}

# A row that names an aggregate rather than a day. Committing one would store a
# month's total as a single service day, so these are skipped rather than
# treated as an unreadable date the operator is asked to fix.
SUMMARY_LABEL = re.compile(r"\b(total|subtotal|sub-total|sum|average|avg|mean)\b", re.IGNORECASE)

# Cell contents that mean "nothing here", not "unparseable".
BLANK_VALUES: frozenset[str] = frozenset(
    {"", "-", "--", "—", "n/a", "na", "nil", "null", "none", "tbd", "?"}
)

# How many canonical fields a row must name before it is believed to be the
# header row: one is a title ("Visitors per day"), two is a table.
HEADER_MIN_FIELDS = 2

# ---------------------------------------------------------------------------
# Encoding / delimiter
# ---------------------------------------------------------------------------


def detect_encoding(data: bytes) -> str:
    """Detect text encoding for CSV bytes.

    Tries UTF-8 (with and without BOM), then falls back to latin-1 so that
    the caller always gets a decodable string. The result is suitable for
    ``bytes.decode(encoding)``.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "latin-1"


def sniff_delimiter(sample: str) -> str:
    """Sniff CSV delimiter from a text sample.

    Uses :mod:`csv.Sniffer` when possible; falls back to frequency heuristics
    so that single-line files still yield a usable delimiter.
    """
    try:
        sniffer = csv.Sniffer()
        dialect = sniffer.sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except Exception:
        counts = {d: sample.count(d) for d in [",", ";", "\t", "|"]}
        best = max(counts, key=lambda k: counts[k])
        return best if counts[best] > 0 else ","


# ---------------------------------------------------------------------------
# Header detection
# ---------------------------------------------------------------------------


def normalise_header(raw: Any) -> str:
    """A header reduced to the words that identify it.

    ``"Visitors (adults)"`` -> ``"visitors"``, ``"No. of guests"`` -> ``"no of
    guests"``, ``"Date_ISO"`` -> ``"date iso"``. Accent folding keeps a French
    or Spanish export matching the same alias list.
    """
    text = unicodedata.normalize("NFKD", str(raw or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"\([^)]*\)", " ", text)  # units in parentheses
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def header_field(cell: Any) -> str | None:
    """The canonical field a header cell names, or ``None``.

    Whole-word matching, ranked so that a column is named after the word it
    *starts* with: ``"Comment on demand"`` is notes (a comment about demand),
    not demand. Ties fall back to the earlier alias.
    """
    norm = normalise_header(cell)
    if not norm:
        return None
    tokens = norm.split()
    best: tuple[tuple[int, int, int], str] | None = None
    for canonical, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if " " in alias:
                pos = norm.find(alias)
                if pos < 0:
                    continue
            else:
                pos = tokens.index(alias) if alias in tokens else -1
                if pos < 0:
                    continue
            score = (0 if pos == 0 else 1, pos, -len(alias))
            if best is None or score < best[0]:
                best = (score, canonical)
    return best[1] if best is not None else None


def cell_is_blank(value: Any) -> bool:
    """True for a cell that holds no data — including the many ways a log says so."""
    if value is None:
        return True
    return str(value).strip().lower() in BLANK_VALUES


def _cell_date(value: Any) -> datetime.date | None:
    try:
        return parse_date_value(value)
    except (TypeError, ValueError):
        return None


def row_field_hits(row: list[Any]) -> set[str]:
    """The canonical fields a row's cells name (used to spot the header row)."""
    fields = {header_field(cell) for cell in row if not cell_is_blank(cell)}
    fields.discard(None)
    return fields  # type: ignore[return-value]


def detect_header_row(matrix: list[list[Any]], *, max_scan: int = 25) -> int:
    """Index of the row that names the columns.

    Exports and notebooks put a title, a branch name and a date range above the
    table, so "row 1 is the header" turned a four-column file into a single
    column named after the restaurant — and every column below it unmappable.

    A header row names at least :data:`HEADER_MIN_FIELDS` canonical fields and
    carries no dates; the first row that qualifies is the table's header. A file
    whose column names are all unfamiliar falls back to row 0, which is the
    behaviour the mapping screen was built around.
    """
    for index, row in enumerate(matrix[:max_scan]):
        cells = [cell for cell in row if not cell_is_blank(cell)]
        if len(cells) < HEADER_MIN_FIELDS:
            continue
        if any(_cell_date(cell) is not None for cell in cells):
            continue
        if len(row_field_hits(row)) >= HEADER_MIN_FIELDS:
            return index
    return 0


def _unique_headers(cells: list[Any]) -> list[str]:
    """Header names that a row dict can actually be keyed by.

    Blank cells get a positional name and repeats get a suffix: two columns
    called ``Visitors`` must both be visible in the mapping dropdown instead of
    one silently overwriting the other.
    """
    headers: list[str] = []
    counts: dict[str, int] = {}
    for index, cell in enumerate(cells):
        name = str(cell).strip() if cell is not None else ""
        if not name:
            name = f"column_{index + 1}"
        count = counts.get(name, 0) + 1
        counts[name] = count
        headers.append(name if count == 1 else f"{name} ({count})")
    return headers


def suggest_column_map(headers: list[str]) -> dict[str, str]:
    """Best-guess mapping from canonical field to header, for the mapping UI.

    The screen used to guess with ``header.includes("date")`` on raw names, so a
    file saying ``Visitors (adults)`` and ``No. of guests`` arrived unmapped and
    the operator had to guess which dropdown meant what. One header is never
    suggested for two fields.
    """
    suggested: dict[str, str] = {}
    used: set[str] = set()
    for canonical in ("date", "demand_qty", "location", "notes"):
        for header in headers:
            if header in used:
                continue
            if header_field(header) == canonical:
                suggested[canonical] = header
                used.add(header)
                break
    return suggested


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------


def parse_date_value(raw: Any) -> datetime.date:
    """Parse a raw cell value into a :class:`datetime.date`.

    Accepts :class:`datetime.date`, :class:`datetime.datetime`, Excel serial
    numbers (as float/int), and the common string formats listed in
    ``DATE_FORMATS``.

    Raises:
        ValueError: If the value cannot be interpreted as a date.
    """
    if isinstance(raw, datetime.datetime):
        return raw.date()
    if isinstance(raw, datetime.date):
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        # Excel serial — 1899-12-30 epoch (with Lotus 1900 bug not corrected;
        # good enough for staging preview).
        if 30000 < raw < 60000:  # plausible Excel serial range
            base = datetime.date(1899, 12, 30)
            return base + datetime.timedelta(days=int(raw))
        raise ValueError(f"Numeric date out of range: {raw!r}")

    text = str(raw).strip()
    if not text:
        raise ValueError("Empty date value")
    # Written dates carry decoration the formats cannot: ordinals ("3rd Dec
    # 2026"), footnote stars and trailing punctuation.
    text = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[*\u2022\u00b9\u00b2\u00b3]", " ", text)
    text = re.sub(r"\s+", " ", text).strip().strip(",;")

    for fmt in DATE_FORMATS:
        try:
            # DATE_FORMATS are date-only, and the time component is discarded by
            # ``.date()`` on the next line, so there is no naive-vs-aware
            # ambiguity here to resolve (the input has no zone to parse).
            return datetime.datetime.strptime(text, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue

    # A bare five-digit number is an Excel serial that a CSV export left as
    # text (the same value arrives as an int/float from a real workbook).
    if re.fullmatch(r"\d{5}", text):
        serial = int(text)
        if 30000 < serial < 60000:
            return datetime.date(1899, 12, 30) + datetime.timedelta(days=serial)

    # Last resort: split on non-digits and try Y-M-D / D/M/Y heuristics.
    parts = re.split(r"[^0-9]+", text)
    parts = [p for p in parts if p]
    if len(parts) == 3:
        try:
            # Prefer Y-M-D when first part is 4 digits.
            if len(parts[0]) == 4:
                return datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            # Otherwise D/M/Y (or M/D/Y when the day cannot be a month).
            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
            if year < 100:
                year += 2000
            if month > 12 >= day:
                day, month = month, day
            return datetime.date(year, month, day)
        except ValueError:
            pass

    raise ValueError(f"Unparseable date: {raw!r}")


def parse_int_value(raw: Any) -> int:
    """Parse a demand quantity cell into a non-negative int.

    Written and exported counts come decorated: ``1,234``, ``1 234``,
    ``1.234`` (European thousands), ``RWF 1,234``, ``12.5``. Separators are
    stripped before the number is read, and a group of exactly three digits
    after the first is treated as thousands rather than a decimal.
    """
    if isinstance(raw, bool):
        raise ValueError(f"demand_qty must be an integer, got {raw!r}")
    if isinstance(raw, int):
        val = raw
    elif isinstance(raw, float):
        if raw.is_integer():
            val = int(raw)
        else:
            raise ValueError(f"demand_qty must be an integer, got {raw!r}")
    else:
        text = str(raw).strip()
        if not text:
            raise ValueError("Empty demand_qty")
        text = re.sub(r"[\s\u00a0\u202f']", "", text)  # 1 034 / 1'034
        text = re.sub(r"(?i)\b(rwf|usd|eur|kes|ugx|tzs|gbp)\b", "", text)
        text = re.sub(r"[^\d.,\-]", "", text)  # currency symbols, units
        text = text.strip(".,")
        if not text:
            raise ValueError(f"Unparseable demand_qty: {raw!r}")
        # A cents tail (`1,234.00`) is not a thousands separator: peel it off,
        # then decide whether the rest is grouped thousands or one decimal.
        tail = ""
        match = re.fullmatch(r"(-?[\d.,]*?)([.,]\d{1,2})", text)
        if match and re.fullmatch(r"-?\d{1,3}(?:[.,]\d{3})+", match.group(1)):
            tail = "." + match.group(2)[1:]
            text = match.group(1)
        if re.fullmatch(r"-?\d{1,3}(?:[.,]\d{3})+", text):
            text = re.sub(r"[.,]", "", text)  # 1,234 / 1.234.567
        else:
            text = text.replace(",", ".")
        text += tail
        try:
            val = int(float(text))
        except ValueError as exc:
            raise ValueError(f"Unparseable demand_qty: {raw!r}") from exc
    if val < 0:
        raise ValueError("demand_qty must be >= 0")
    return val


# ---------------------------------------------------------------------------
# File parsing
# ---------------------------------------------------------------------------


def _csv_matrix(data: bytes) -> list[list[Any]]:
    """Every CSV row as a cell list, before any header is chosen.

    ``csv.reader`` rather than ``DictReader`` on purpose: the reader is what
    makes it possible to *find* the header instead of assuming row 1 is it.
    Short rows are padded when the row dicts are built, so a ragged export does
    not silently drop its tail columns.
    """
    enc = detect_encoding(data)
    text = data.decode(enc)
    if text.startswith("\ufeff"):  # BOM surviving the latin-1 fallback
        text = text[1:]
    delimiter = sniff_delimiter(text[:4096])
    matrix: list[list[Any]] = []
    for raw in csv.reader(io.StringIO(text), delimiter=delimiter):
        matrix.append([cell.strip() if isinstance(cell, str) else cell for cell in raw])
    return matrix


def _xlsx_matrix(data: bytes) -> list[list[Any]]:
    # Deferred import so CSV-only deployments do not require openpyxl.
    try:
        import openpyxl  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ValueError("XLSX parsing requires 'openpyxl' (pip install openpyxl)") from exc

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    if ws is None:
        raise ValueError("XLSX workbook has no active sheet")

    matrix: list[list[Any]] = []
    for values in ws.iter_rows(values_only=True):
        if values is None:
            continue
        # Keep native types: an Excel date or serial is only parseable as one.
        matrix.append(["" if value is None else value for value in values])
    if not matrix:
        raise ValueError("XLSX sheet is empty")
    return matrix


@dataclass(frozen=True, slots=True)
class ParsedTable:
    """A file read the way a person would read it: header, then data."""

    headers: list[str]
    rows: list[dict[str, Any]]
    #: 0-based file row the headers came from (0 for a well-formed export).
    header_row: int

    @property
    def preamble_rows(self) -> int:
        """Title/window lines above the header, reported to the operator."""
        return self.header_row


def _align_cells(cells: list[Any], headers: list[str]) -> list[Any]:
    """Fit a row's cells to the header count, repairing the common shifts.

    More cells than headers means either a genuinely extra column or an
    unquoted comma inside a written date: ``Dec 3, 2025`` is three CSV cells and
    one date, and every column after it shifts by one. The shift is repaired
    only when joining the leading cells produces a real date, which is a strong
    enough signal to be safe; otherwise the tail is dropped, as before.
    """
    if len(cells) <= len(headers):
        return list(cells) + [""] * (len(headers) - len(cells))
    for span in (2, 3):
        joined = ", ".join(str(cell).strip() for cell in cells[:span] if not cell_is_blank(cell))
        if _cell_date(joined) is None:
            continue
        candidate = [joined, *cells[span:]]
        if len(candidate) == len(headers):
            return candidate
    return cells[: len(headers)]


def _rows_from_matrix(
    matrix: list[list[Any]], headers: list[str], start: int
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in matrix[start:]:
        if all(cell_is_blank(cell) for cell in raw):
            continue
        cells = _align_cells(raw, headers)
        row: dict[str, Any] = {}
        for index, header in enumerate(headers):
            value = cells[index] if index < len(cells) else ""
            row[header] = value.strip() if isinstance(value, str) else value
        rows.append(row)
    return rows


def parse_file_table(filename: str, data: bytes) -> ParsedTable:
    """Parse a CSV/XLSX into headers, rows, and where the header was found."""
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xls")):
        matrix = _xlsx_matrix(data)
    elif lower.endswith((".csv", ".txt")):
        matrix = _csv_matrix(data)
    else:
        try:
            matrix = _csv_matrix(data)
        except Exception:
            matrix = _xlsx_matrix(data)

    if not matrix:
        raise ValueError("File is empty")
    header_row = detect_header_row(matrix)
    headers = _unique_headers(matrix[header_row])
    rows = _rows_from_matrix(matrix, headers, header_row + 1)
    return ParsedTable(headers=headers, rows=rows, header_row=header_row)


def parse_file_content(filename: str, data: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    """Parse raw file bytes into ``(headers, rows)``.

    Args:
        filename: Original filename (used to infer type).
        data: Raw file bytes.

    Returns:
        Tuple of ``(detected_headers, list_of_row_dicts)``.
    """
    table = parse_file_table(filename, data)
    return table.headers, table.rows


def parse_file(filename: str, data: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    """Alias for :func:`parse_file_content` (public contract)."""
    return parse_file_content(filename, data)


# ---------------------------------------------------------------------------
# Column mapping
# ---------------------------------------------------------------------------


def _is_summary_row(raw: dict[str, Any], date_col: str | None, qty_col: str | None) -> bool:
    """True for a total/average line that must not become a service day."""
    for column in (date_col, qty_col):
        value = raw.get(column) if column else None
        if isinstance(value, str) and SUMMARY_LABEL.search(value):
            return True
    return False


def apply_column_map(
    rows: list[dict[str, Any]],
    column_map: dict[str, str],
    *,
    default_location_id: uuid.UUID | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply a user-supplied column map to parsed rows.

    The map keys are canonical field names (``date``, ``demand_qty``,
    ``location``, ``notes``) and values are header names from the file.
    ``location`` may be omitted when the caller supplies
    ``default_location_id``.

    Messy files carry lines that are not data — a blank spacer, a ``TOTAL``
    line, a ``N/A`` week, the title that preceded the header. Those are
    *skipped* and named as such, separately from ``errors``, because a single
    footer must not make a clean file uncommittable: the operator needs the
    difference between "these rows are broken" and "these rows were never
    data".

    Returns:
        ``(mapped_rows, errors, skipped_rows)``. Mapped rows carry ``date``,
        ``demand_qty``, ``location`` and ``notes``; errors are
        ``{row_index, field, message}``; skipped are ``{row_index, reason}``.
    """
    mapped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    date_col = column_map.get("date")
    qty_col = column_map.get("demand_qty")
    loc_col = column_map.get("location")
    notes_col = column_map.get("notes")

    if not date_col or not qty_col:
        raise ValueError("column_map must include 'date' and 'demand_qty'")

    for idx, raw in enumerate(rows):
        row_errors: list[dict[str, Any]] = []
        raw_date = raw.get(date_col) if date_col else None
        raw_qty = raw.get(qty_col) if qty_col else None

        # Not data: a summary line, or a row with neither a day nor a count.
        # Checked before parsing so a "TOTAL 4131" line is reported as skipped
        # rather than as a date the operator is asked to fix.
        if _is_summary_row(raw, date_col, qty_col):
            skipped.append({"row_index": idx, "reason": "summary_row"})
            continue
        if cell_is_blank(raw_date) and cell_is_blank(raw_qty):
            skipped.append({"row_index": idx, "reason": "no_date_or_count"})
            continue

        # Date
        try:
            parsed_date = parse_date_value(raw_date)
        except Exception as exc:
            row_errors.append({"row_index": idx, "field": "date", "message": str(exc)})
            parsed_date = None  # type: ignore[assignment]

        # Demand
        try:
            parsed_qty = parse_int_value(raw_qty)
        except Exception as exc:
            row_errors.append({"row_index": idx, "field": "demand_qty", "message": str(exc)})
            parsed_qty = None  # type: ignore[assignment]

        # Location (optional when default supplied)
        location_ref: Any = None
        if loc_col:
            location_ref = raw.get(loc_col)
            if isinstance(location_ref, str):
                location_ref = location_ref.strip()
        elif default_location_id is not None:
            location_ref = str(default_location_id)

        # Notes
        notes_val: str | None = None
        if notes_col and raw.get(notes_col) is not None:
            notes_val = str(raw.get(notes_col)).strip() or None

        if row_errors:
            errors.extend(row_errors)
            continue

        mapped.append(
            {
                "row_index": idx,
                "date": parsed_date,
                "demand_qty": parsed_qty,
                "location": location_ref,
                "notes": notes_val,
                "_raw": raw,
            }
        )

    return mapped, errors, skipped
