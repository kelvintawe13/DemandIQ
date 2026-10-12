#!/usr/bin/env python3
"""Ingest the URBAN-WASTE Food Tracking Device dataset into ``waste_observations``.

Why this exists
---------------
Chapter 3.21 establishes URBAN-WASTE as an *evidence boundary*: waste
observations quantify food waste in kitchens, but they are never joined to
``demand_observations`` — the table has no foreign key into the demand
schema, and this loader keeps it that way (it touches only
``waste_observations``).

The source file (Zenodo record 3355300, CC BY 4.0, 3,321 records across
European/Japanese establishments) is a semicolon-separated CSV with
continental conventions this normalises:

* ``Waste weight sum`` is comma-decimal (``33,89``) → float kg.
* ``Timestamp`` is DD/MM/YYYY → real ``date`` objects.
* ``ordered portions, Whole day`` is sparse (9 non-empty values of 3,321).
* Empty cells stay ``NULL`` — nothing is imputed.

Establishment naming
--------------------
The CSV has no establishment code, only ``City / establishment name`` (e.g.
``Santander / El Italiano Trattoria Pizzeria``). That composite becomes the
``establishment_code`` — stable, greppable, and unique per site.

Usage:
    backend/.venv/bin/python backend/scripts/ingest_urban_waste.py \
        [--csv path] [--replace]
"""

from __future__ import annotations

import argparse
import csv
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = REPO_ROOT / "model" / "data" / "urban-waste" / "Food Tracking Device dataset.csv"


def parse_float_comma(raw: str) -> float | None:
    """``33,89`` → 33.89. Empty → None. Already-dot decimals pass through."""
    value = raw.strip().replace("\u00a0", "").replace('"', "")
    if not value:
        return None
    return float(value.replace(",", "."))


def parse_date_ddmmyyyy(raw: str):
    value = raw.strip()
    if not value:
        return None
    # The CSV cells carry a bare calendar date (no time-of-day, no zone), so
    # parse to a naive datetime and immediately reduce to ``date`` — the
    # timezone-less constructor is consumed before anything can observe it.
    return datetime.strptime(value, "%d/%m/%Y").replace(tzinfo=UTC).date()


def parse_int(raw: str) -> int | None:
    value = raw.strip()
    if not value:
        return None
    return int(float(value.replace(",", ".")))


def load_rows(csv_path: Path) -> list[dict]:
    # The published file's non-ASCII bytes (Portuguese establishment names,
    # e.g. "Onj São Lázaro") are original Latin-1 in an ASCII-pretty file;
    # Zenodo's checksum only changes if a byte changes, so decode permissively
    # and normalise to NFC instead of replacing characters or dropping rows.
    raw = csv_path.read_bytes().decode("latin-1")
    reader = csv.DictReader(raw.splitlines(), delimiter=";")
    rows = []
    for row in reader:
        establish = unicodedata.normalize("NFC", (row.get("City / kitchen") or "")).strip()
        ts = (row.get("Timestamp") or "").strip()
        waste = (row.get("Waste weight sum") or "").strip()
        whole_day = (row.get("ordered portions, Whole day") or "").strip()
        if not establish and not waste:
            continue  # blank padding row
        rows.append(
            {
                "establishment_code": establish or "unknown",
                "country": (establish.split(" / ")[0].strip() if " / " in establish else None),
                "observed_on": parse_date_ddmmyyyy(ts),
                "waste_kg": parse_float_comma(waste),
                "ordered_portions": parse_int(whole_day),
                "source_dataset": "urban-waste-ftd",
                "licence": "CC BY 4.0",
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest URBAN-WASTE FTD csv.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing urban-waste-ftd rows first (idempotent refresh).",
    )
    args = parser.parse_args()

    import os

    settings_url = os.environ.get("DATABASE_URL")
    if not settings_url:
        from app.config import get_settings  # noqa: F401  — validates presence

        settings_url = get_settings().database_url
    engine = sa.create_engine(settings_url)

    rows = load_rows(args.csv)
    if not rows:
        print("No usable rows found in", args.csv)
        return 1

    insert_sql = sa.text("""
        INSERT INTO waste_observations
            (establishment_code, country, observed_on, waste_kg, ordered_portions,
             source_dataset, licence)
        VALUES
            (:establishment_code, :country, :observed_on, :waste_kg, :ordered_portions,
             :source_dataset, :licence)
        """)
    delete_sql = sa.text("DELETE FROM waste_observations WHERE source_dataset = 'urban-waste-ftd'")

    with engine.begin() as conn:
        if args.replace:
            deleted = conn.execute(delete_sql).rowcount
            print(f"--replace: removed {deleted} prior urban-waste-ftd rows")
        # Natural-key dedupe within the file itself.
        conn.execute(sa.text("SET TIME ZONE 'UTC'"))
        result = conn.execute(insert_sql, rows)
        print(f"Inserted {result.rowcount} rows")

    ests = {r["establishment_code"] for r in rows}
    dated = sum(1 for r in rows if r["observed_on"] is not None)
    wasted = sum(1 for r in rows if r["waste_kg"] is not None)
    portions = sum(1 for r in rows if r["ordered_portions"] is not None)
    print(
        f"establishments: {len(ests)} | dated: {dated}/{len(rows)} | "
        f"waste_kg populated: {wasted}/{len(rows)} | ordered_portions populated: {portions}/{len(rows)}"
    )
    print("Evidence boundary intact: only waste_observations written, no demand join.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
