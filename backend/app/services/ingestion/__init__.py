"""Ingestion subpackage — adapters and shared normaliser.

All inbound channels terminate at :func:`commit_demand` so that idempotency,
future-date rejection and source tagging cannot be bypassed.
"""

from __future__ import annotations

from app.services.ingestion.file import (
    apply_column_map,
    detect_encoding,
    parse_file,
    parse_file_content,
    sniff_delimiter,
)
from app.services.ingestion.image import extract_rows_from_image, parse_ocr_text
from app.services.ingestion.normaliser import CommitResult, commit_demand

__all__ = [
    "CommitResult",
    "apply_column_map",
    "commit_demand",
    "detect_encoding",
    "extract_rows_from_image",
    "parse_ocr_text",
    "parse_file",
    "parse_file_content",
    "sniff_delimiter",
]
