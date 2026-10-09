"""Unit tests for the OCR parser, drift scoring and job scheduling.

These are pure-function tests (no DB) so they stay hermetic, mirroring the rest
of the suite. They cover the three surfaces that were previously stubbed:
fabricated OCR rows, hardcoded drift signals, and a placeholder jobs module.
"""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.jobs.runner import JOBS, enqueue_run, run_job
from app.jobs.scheduler import cron_matches, due_jobs
from app.services.drift import population_stability_index
from app.services.ingestion.image import parse_ocr_text, preview_for_image

# --------------------------------------------------------------- OCR parser ---


def test_parse_ocr_text_extracts_date_and_count() -> None:
    rows = parse_ocr_text("12/01/2026   112\n13/01/2026   87\n")
    assert [r["date"] for r in rows] == ["2026-01-12", "2026-01-13"]
    assert [r["demand_qty"] for r in rows] == [112, 87]
    assert rows[0]["row_index"] == 0
    assert 0.0 < rows[0]["confidence"] <= 1.0


def test_parse_ocr_text_handles_iso_and_text_month() -> None:
    rows = parse_ocr_text("2026-01-12 112\n3 Jan 2026 44")
    assert [r["date"] for r in rows] == ["2026-01-12", "2026-01-03"]
    assert [r["demand_qty"] for r in rows] == [112, 44]


def test_parse_ocr_text_skips_lines_without_both_fields() -> None:
    # No date on the first line, no count on the second — neither is guessed at.
    rows = parse_ocr_text("Total covers 112\n14/01/2026   --")
    assert rows == []


def test_ocr_preview_never_fabricates_rows_for_garbage_input() -> None:
    # A non-image blob cannot be OCR'd; the adapter must return an empty
    # preview with an honest note rather than an invented demand row.
    preview = preview_for_image(b"not-an-image", "log.jpg")
    assert preview["rows"] == []
    assert preview["row_count"] == 0
    assert "note" in preview and preview["note"]


def test_ocr_preview_names_the_engine_that_read_the_photo() -> None:
    """The engine travels with the rows, because it decides what `confidence` means.

    A remote vision service returns a model score; the local parser's number
    counts how many numbers were left on the line. A review screen that showed
    both as a percentage would dress a readability heuristic up as a probability,
    so it has to know which one it is looking at.
    """
    from app.services.ingestion.image import ENGINE_NONE, ENGINE_TESSERACT

    # Nothing can read this blob, and no engine is configured in the unit suite.
    preview = preview_for_image(b"not-an-image", "log.jpg")
    assert preview["engine"] == ENGINE_NONE
    # ...and the note says why, rather than implying the photo was blank.
    assert "engine" in preview["note"].lower() or "configured" in preview["note"].lower()

    # With Tesseract available the engine is reported as the local parser.
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(
            "app.services.ingestion.image._extract_via_tesseract",
            lambda content, language: parse_ocr_text("12/01/2026   112"),
        )
        preview = preview_for_image(b"pretend-image", "log.jpg")
        assert preview["engine"] == ENGINE_TESSERACT
        assert preview["row_count"] == 1
    finally:
        monkeypatch.undo()


def test_parser_confidence_is_about_line_legibility_not_probability() -> None:
    """0.9 means "one number on the line"; 0.75 means the count was a choice.

    Pinned here because the review screen words these in the operator's terms
    ("One number on the line" / "Several numbers — check"), so the values are a
    contract between the parser and that wording, not arbitrary numbers.
    """
    from app.services.ingestion.image import (
        CONFIDENCE_AMBIGUOUS_LINE,
        CONFIDENCE_UNAMBIGUOUS_LINE,
    )

    unambiguous = parse_ocr_text("12/01/2026   112")[0]
    assert unambiguous["confidence"] == CONFIDENCE_UNAMBIGUOUS_LINE

    # A trailing note leaves a second integer behind, so the last one was taken
    # for the operator — worth flagging before it is committed.
    ambiguous = parse_ocr_text("12/01/2026   112   tips 3")[0]
    assert ambiguous["demand_qty"] == 3
    assert ambiguous["confidence"] == CONFIDENCE_AMBIGUOUS_LINE
    assert ambiguous["confidence"] < CONFIDENCE_UNAMBIGUOUS_LINE


# -------------------------------------------------------------------- drift ---


def test_psi_zero_for_identical_distributions() -> None:
    sample = [10.0, 20.0, 30.0, 40.0, 50.0] * 10
    psi = population_stability_index(sample, list(sample))
    assert psi is not None
    assert psi == pytest.approx(0.0, abs=1e-9)


def test_psi_high_for_shifted_distribution() -> None:
    baseline = [10.0, 20.0, 30.0, 40.0, 50.0] * 10
    shifted = [v * 3 for v in baseline]
    psi = population_stability_index(baseline, shifted)
    assert psi is not None
    assert psi > 0.2  # well past the "drifting" threshold


def test_psi_none_without_samples() -> None:
    assert population_stability_index([], [1.0, 2.0]) is None
    assert population_stability_index([1.0], []) is None


# ---------------------------------------------------------------- scheduler ---


def test_cron_matches_field_patterns() -> None:
    at_10_15 = datetime.datetime(2026, 9, 21, 10, 15, tzinfo=datetime.UTC)
    assert cron_matches("*/15 * * * *", at_10_15)
    assert not cron_matches("*/15 * * * *", at_10_15.replace(minute=16))
    assert cron_matches("0 * * * *", at_10_15.replace(minute=0))
    assert not cron_matches("0 * * * *", at_10_15)
    assert cron_matches("40 3 * * *", at_10_15.replace(hour=3, minute=40))
    assert not cron_matches("nonsense", at_10_15)


def test_cron_day_of_week_uses_sunday_zero() -> None:
    # Scan a few consecutive days and pick the Sunday, so the test does not
    # depend on a hardcoded calendar fact.
    days = [datetime.date(2026, 9, 19) + datetime.timedelta(days=i) for i in range(7)]
    sunday = next(d for d in days if d.weekday() == 6)
    monday = next(d for d in days if d.weekday() == 0)
    assert cron_matches("0 2 * * 0", datetime.datetime.combine(sunday, datetime.time(2, 0)))
    assert not cron_matches("0 2 * * 0", datetime.datetime.combine(monday, datetime.time(2, 0)))


def test_due_jobs_at_feature_materialisation_minute() -> None:
    when = datetime.datetime(2026, 9, 21, 3, 20, tzinfo=datetime.UTC)
    due = due_jobs(when)
    assert "feature_materialisation" in due
    assert "recalibration" not in due


def test_every_job_has_a_schedule_and_runner() -> None:
    from app.jobs.runner import JOB_SCHEDULE

    assert set(JOB_SCHEDULE) == set(JOBS)


# -------------------------------------------------------------- job runner ---


def test_enqueue_and_run_reject_unknown_job_type() -> None:
    with pytest.raises(ValueError):
        enqueue_run(None, workspace_id=uuid.uuid4(), job_type="nope")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        run_job(None, workspace_id=uuid.uuid4(), job_type="nope")  # type: ignore[arg-type]
