"""Manual ingestion helpers.

The router validates via Pydantic; this module contains the small amount of
shared coercion that is also reused by the file/image commit paths.
"""

from __future__ import annotations

from typing import Any

from app.services.ingestion.file import parse_date_value, parse_int_value


def coerce_manual_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a single manual payload row into canonical types.

    Args:
        raw: Dict with keys ``date``, ``demand_qty`` (and optionally
            ``location_id``, ``notes``).

    Returns:
        Dict with ``date`` as :class:`datetime.date` and ``demand_qty`` as
        ``int``.

    Raises:
        ValueError: If ``date`` or ``demand_qty`` cannot be parsed.
    """
    date_val = parse_date_value(raw.get("date"))
    qty_val = parse_int_value(raw.get("demand_qty"))
    return {
        "date": date_val,
        "demand_qty": qty_val,
        "location_id": raw.get("location_id"),
        "notes": raw.get("notes"),
    }


def validate_notes(notes: Any) -> str | None:
    """Normalise the optional notes field."""
    if notes is None:
        return None
    text = str(notes).strip()
    if not text:
        return None
    if len(text) > 500:
        raise ValueError("notes must be <= 500 characters")
    return text
