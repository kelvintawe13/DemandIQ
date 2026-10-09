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
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# Allowed column-map keys and their canonical targets.
CANONICAL_FIELDS: frozenset[str] = frozenset({"date", "demand_qty", "location", "notes"})
DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%Y.%m.%d",
    "%d.%m.%Y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)

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

    # Try ISO first (handles YYYY-MM-DD and variants with time).
    for fmt in DATE_FORMATS:
        try:
            # DATE_FORMATS are date-only, and the time component is discarded by
            # ``.date()`` on the next line, so there is no naive-vs-aware
            # ambiguity here to resolve (the input has no zone to parse).
            return datetime.datetime.strptime(text, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue

    # Last resort: split on non-digits and try Y-M-D / D/M/Y heuristics.
    parts = re.split(r"[^0-9]+", text)
    parts = [p for p in parts if p]
    if len(parts) == 3:
        try:
            # Prefer Y-M-D when first part is 4 digits.
            if len(parts[0]) == 4:
                return datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            # Otherwise D/M/Y.
            return datetime.date(int(parts[2]), int(parts[1]), int(parts[0]))
        except ValueError:
            pass

    raise ValueError(f"Unparseable date: {raw!r}")


def parse_int_value(raw: Any) -> int:
    """Parse a demand quantity cell into a non-negative int."""
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, float):
        if raw.is_integer():
            return int(raw)
        raise ValueError(f"demand_qty must be an integer, got {raw!r}")
    text = str(raw).strip().replace(",", "").replace(" ", "")
    if not text:
        raise ValueError("Empty demand_qty")
    # Allow "1,200" / "1 200" already stripped.
    try:
        val = int(float(text)) if "." in text else int(text)
    except ValueError as exc:
        raise ValueError(f"Unparseable demand_qty: {raw!r}") from exc
    if val < 0:
        raise ValueError("demand_qty must be >= 0")
    return val


# ---------------------------------------------------------------------------
# File parsing
# ---------------------------------------------------------------------------


def _parse_csv_bytes(data: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    enc = detect_encoding(data)
    text = data.decode(enc)
    # Strip BOM if present after latin-1 fallback.
    if text.startswith("\ufeff"):
        text = text[1:]
    delimiter = sniff_delimiter(text[:4096])
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if reader.fieldnames is None:
        raise ValueError("CSV has no header row")
    headers = [h.strip() for h in reader.fieldnames if h is not None]
    # Normalise header keys in rows.
    rows: list[dict[str, Any]] = []
    for raw in reader:
        # Skip fully empty rows.
        if not any(v and str(v).strip() for v in raw.values()):
            continue
        row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items() if k}
        rows.append(row)
    return headers, rows


def _parse_xlsx_bytes(data: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    # Deferred import so CSV-only deployments do not require openpyxl.
    try:
        import openpyxl  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ValueError("XLSX parsing requires 'openpyxl' (pip install openpyxl)") from exc

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    if ws is None:
        raise ValueError("XLSX workbook has no active sheet")

    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        raise ValueError("XLSX sheet is empty") from None

    headers = [str(c).strip() if c is not None else f"col_{i}" for i, c in enumerate(header_row)]
    rows: list[dict[str, Any]] = []
    for values in rows_iter:
        if values is None or all(v is None or str(v).strip() == "" for v in values):
            continue
        row = {headers[i]: values[i] for i in range(min(len(headers), len(values)))}
        # Normalise None -> "" for consistency with CSV path.
        row = {k: ("" if v is None else v) for k, v in row.items()}
        rows.append(row)
    return headers, rows


def parse_file_content(filename: str, data: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    """Parse raw file bytes into ``(headers, rows)``.

    Args:
        filename: Original filename (used to infer type).
        data: Raw file bytes.

    Returns:
        Tuple of ``(detected_headers, list_of_row_dicts)``.
    """
    lower = filename.lower()
    if lower.endswith(".xlsx") or lower.endswith(".xls"):
        return _parse_xlsx_bytes(data)
    if lower.endswith(".csv") or lower.endswith(".txt"):
        return _parse_csv_bytes(data)
    # Fallback: try CSV first, then XLSX.
    try:
        return _parse_csv_bytes(data)
    except Exception:
        return _parse_xlsx_bytes(data)


def parse_file(filename: str, data: bytes) -> tuple[list[str], list[dict[str, Any]]]:
    """Alias for :func:`parse_file_content` (public contract)."""
    return parse_file_content(filename, data)


# ---------------------------------------------------------------------------
# Column mapping
# ---------------------------------------------------------------------------


def apply_column_map(
    rows: list[dict[str, Any]],
    column_map: dict[str, str],
    *,
    default_location_id: uuid.UUID | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply a user-supplied column map to parsed rows.

    The map keys are canonical field names (``date``, ``demand_qty``,
    ``location``, ``notes``) and values are header names from the file.
    ``location`` may be omitted when the caller supplies
    ``default_location_id``.

    Returns:
        ``(mapped_rows, errors)`` where each mapped row has keys
        ``date``, ``demand_qty``, ``location_id``/``location``, ``notes``
        and errors are ``{row_index, field, message}`` dicts.
    """
    mapped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    date_col = column_map.get("date")
    qty_col = column_map.get("demand_qty")
    loc_col = column_map.get("location")
    notes_col = column_map.get("notes")

    if not date_col or not qty_col:
        raise ValueError("column_map must include 'date' and 'demand_qty'")

    for idx, raw in enumerate(rows):
        row_errors: list[dict[str, Any]] = []

        # Date
        try:
            raw_date = raw.get(date_col) if date_col else None
            parsed_date = parse_date_value(raw_date)
        except Exception as exc:
            row_errors.append({"row_index": idx, "field": "date", "message": str(exc)})
            parsed_date = None  # type: ignore[assignment]

        # Demand
        try:
            raw_qty = raw.get(qty_col) if qty_col else None
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

    return mapped, errors
