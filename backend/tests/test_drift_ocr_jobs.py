"""Unit tests for the OCR parser, drift scoring and job scheduling.

These are pure-function tests (no DB) so they stay hermetic, mirroring the rest
of the suite. They cover the three surfaces that were previously stubbed:
fabricated OCR rows, hardcoded drift signals, and a placeholder jobs module.
"""

from __future__ import annotations

import base64
import datetime
import uuid

import pytest

from app.jobs.runner import JOB_SCHEDULE, JOBS, enqueue_run, run_job
from app.jobs.scheduler import cron_matches, due_jobs
from app.services.drift import population_stability_index
from app.services.ingestion.image import parse_ocr_text, preview_for_image
from app.services.ingestion.retention import photo_retention_cutoff

# A real 1x1 PNG: the upload route validates content, so a fake byte string is
# no longer a valid stand-in for "an image the OCR engine read nothing from".
_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
)

# ---------------------------------------------------------- photo retention ---


def test_photo_retention_cutoff_is_thirty_days_and_always_utc() -> None:
    """A photo older than 30 days is eligible; the policy states that window."""
    now = datetime.datetime(2026, 10, 29, 12, 0, tzinfo=datetime.UTC)
    assert photo_retention_cutoff(now) == datetime.datetime(
        2026, 9, 29, 12, 0, tzinfo=datetime.UTC
    )
    # A naive reference is treated as UTC rather than silently becoming local.
    assert photo_retention_cutoff(datetime.datetime(2026, 10, 29, 12, 0)) == datetime.datetime(
        2026, 9, 29, 12, 0, tzinfo=datetime.UTC
    )


def test_photo_retention_is_registered_and_scheduled_daily() -> None:
    """The job must be in JOBS and on the cadence table, or it never runs."""
    assert "photo_retention" in JOBS
    assert JOB_SCHEDULE["photo_retention"]["cron"] == "30 2 * * *"
    assert "photo_retention" in due_jobs(
        datetime.datetime(2026, 10, 29, 2, 30, tzinfo=datetime.UTC)
    )


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


def test_parse_ocr_text_handles_month_first_text_dates() -> None:
    """A US-style log writes "Oct 1", not "1 Oct".

    This is the layout operators actually photograph (month name, day, then
    the visitor count), and the parser used to only accept the day-first form,
    so a clear printed sheet came back as zero rows.
    """
    rows = parse_ocr_text("Oct 1 58\nOct 2 47\nOctober 12, 2026 73")
    year = datetime.datetime.now(datetime.UTC).year
    assert [r["date"] for r in rows] == [
        f"{year}-10-01",
        f"{year}-10-02",
        "2026-10-12",
    ]
    assert [r["demand_qty"] for r in rows] == [58, 47, 73]


def test_parse_ocr_text_recovers_a_one_read_as_a_letter() -> None:
    """Tesseract reads a printed 1 as l/I in a month-first date.

    "Oct l" is still unmistakably the first of October to a human, so the
    parser normalises the glyph instead of dropping that day from the sheet.
    """
    rows = parse_ocr_text("Oct l 58\nOct I2 73\nOct l0 40")
    year = datetime.datetime.now(datetime.UTC).year
    assert [r["date"] for r in rows] == [
        f"{year}-10-01",
        f"{year}-10-12",
        f"{year}-10-10",
    ]
    assert [r["demand_qty"] for r in rows] == [58, 73, 40]


def test_parse_ocr_text_does_not_turn_a_bare_month_year_into_a_day() -> None:
    # "October 2026" is a heading, not a row. Reading the first two year digits
    # as a day used to make it look like one.
    assert parse_ocr_text("October 2026\nTotal 112") == []


def test_parse_ocr_text_accepts_month_first_when_unambiguous() -> None:
    """A US date such as 12/31 cannot be day-first, and it sets the sheet order.

    The ambiguous 01/02 row must follow the convention the rest of the photo
    proved, not flip back to the default just because it can be read both ways.
    """
    rows = parse_ocr_text("12/31/2026 100\n01/02/2026 90")
    assert [r["date"] for r in rows] == ["2026-12-31", "2026-01-02"]


def test_parse_ocr_text_accepts_day_first_when_unambiguous() -> None:
    rows = parse_ocr_text("31/12/2026 100\n01/02/2026 90")
    assert [r["date"] for r in rows] == ["2026-12-31", "2026-02-01"]


def test_parse_ocr_text_defaults_ambiguous_numeric_dates_to_day_first() -> None:
    rows = parse_ocr_text("01/02/2026 90")
    assert rows[0]["date"] == "2026-02-01"


def test_a_yearless_date_uses_the_stated_reporting_year() -> None:
    """ "Oct 1" on a page the operator says is 2024 is 2024-10-01.

    The current year was assumed instead, so an archive page imported as this
    year's demand — and nothing downstream could tell it from a real reading.
    """
    rows = parse_ocr_text("Oct 1 58\n3 Jan 44", reporting_year=2024)
    assert [row["date"] for row in rows] == ["2024-10-01", "2024-01-03"]


def test_a_date_with_its_own_year_ignores_the_reporting_year() -> None:
    # The sheet said 2026; the operator's answer only fills a gap.
    row = parse_ocr_text("October 12, 2026 73", reporting_year=2024)[0]
    assert row["date"] == "2026-10-12"


def test_parse_ocr_text_can_be_told_the_date_order() -> None:
    """Callers that know their locale can resolve an otherwise ambiguous row."""
    rows = parse_ocr_text("01/02/2026 90", day_first=False)
    assert rows[0]["date"] == "2026-01-02"


def test_parse_ocr_text_expands_a_two_digit_year_in_either_order() -> None:
    month_first = parse_ocr_text("12/31/26 100")
    day_first = parse_ocr_text("31/12/26 100")
    assert month_first[0]["date"] == "2026-12-31"
    assert day_first[0]["date"] == "2026-12-31"


def test_parse_ocr_text_skips_an_impossible_numeric_date() -> None:
    assert parse_ocr_text("13/13/2026 100") == []


def test_parse_ocr_text_recovers_a_seven_read_as_slash() -> None:
    """Tesseract often reads a terminal 7 as '/': "2/" is a count of 27.

    The row is still flagged ambiguous, because the correction is a heuristic
    and the operator must confirm it rather than trust a 0.9 legibility score.
    """
    rows = parse_ocr_text("2026-10-20   2/")
    assert rows[0]["demand_qty"] == 27
    assert rows[0]["confidence"] < 0.9


def test_parse_ocr_text_skips_lines_without_both_fields() -> None:
    # No date on the first line, no count on the second — neither is guessed at.
    rows = parse_ocr_text("Total covers 112\n14/01/2026   --")
    assert rows == []


def test_parse_ocr_text_ignores_a_labelled_note_beside_the_count() -> None:
    """A table number is not a visitor count.

    "14 Oct 2026   40   Table 6" always meant forty covers, but the parser took
    the last integer on the line and committed six. A wrong count in demand
    history is worse than a missing one: nothing downstream can tell it apart
    from a real day.
    """
    from app.services.ingestion.image import CONFIDENCE_UNAMBIGUOUS_LINE

    row = parse_ocr_text("14 Oct 2026   40   Table 6")[0]
    assert row["demand_qty"] == 40
    # Only one number on the line is a count, so the read is not ambiguous.
    assert row["confidence"] == CONFIDENCE_UNAMBIGUOUS_LINE

    assert parse_ocr_text("12/01/2026   112   tips 3")[0]["demand_qty"] == 112


def test_parse_ocr_text_keeps_a_label_that_names_the_count() -> None:
    # "Visitors 40" *is* the count: only labels that number something else
    # ("Table 6") are skipped, not the word the log uses for the figure.
    assert parse_ocr_text("12/01/2026   Visitors 40")[0]["demand_qty"] == 40


def test_parse_ocr_text_keeps_a_zero_count() -> None:
    """A restaurant that served nobody has not stopped reporting.

    Zero was dropped as if it were unreadable, so a closed day quietly became a
    missing day and the forecast trained on a gap. The platform stores zero —
    ``normaliser.validate_demand_qty`` only refuses a negative — so the parser
    was the one place that lost it.
    """
    rows = parse_ocr_text("12/01/2026   0")
    assert rows[0]["demand_qty"] == 0


def test_parse_ocr_text_reads_a_count_wider_than_six_digits() -> None:
    # The platform ceiling is seven digits; a pattern that stopped at six
    # dropped such a row instead of reporting a count it could not hold.
    assert parse_ocr_text("12/01/2026 1234567")[0]["demand_qty"] == 1234567


def test_ocr_warnings_name_a_date_that_was_read_twice() -> None:
    """Two rows for one day cannot both be stored, so the screen must say so.

    The commit upserts on ``(location_id, date)``, so the second row silently
    replaces the first. That is the right rule, but it must not be invisible in a
    list the operator was asked to check before importing.
    """
    from app.services.ingestion.image import _row_warnings

    warnings = _row_warnings(parse_ocr_text("12/01/2026 112\n12/01/2026 98\n13/01/2026 87"))
    assert [w["code"] for w in warnings] == ["conflicting_date"]
    assert warnings[0]["row_indexes"] == [0, 1]
    assert warnings[0]["demand_qtys"] == [112, 98]
    assert "2026-01-12" in warnings[0]["message"]

    duplicates = _row_warnings(parse_ocr_text("12/01/2026 112\n12/01/2026 112"))
    assert [w["code"] for w in duplicates] == ["duplicate_date"]
    assert _row_warnings(parse_ocr_text("12/01/2026 112")) == []


def test_a_mode_with_more_rows_does_not_beat_a_cleaner_one() -> None:
    """Scoring is coverage and consistency, not volume.

    A page-segmentation mode that splits lines in two can report more rows than
    the mode that read the page correctly; committing its duplicate dates would
    be worse than the missing day it hides.
    """
    from app.services.ingestion.image import _extraction_score

    clean = parse_ocr_text("\n".join(f"{day:02d}/01/2026 {100 + day}" for day in range(1, 21)))
    twice = "\n".join(f"{day:02d}/01/2026 {100 + day}" for day in range(1, 12)) + "\n"
    split = parse_ocr_text(twice * 2)

    assert len(split) > len(clean)
    assert _extraction_score(clean, 0.8) > _extraction_score(split, 0.9)
    # Word confidence only breaks a tie between otherwise equal reads.
    assert _extraction_score(clean, 0.9) > _extraction_score(clean, 0.8)


def test_ocr_plausibility_ceiling_matches_the_platform_ceiling() -> None:
    """The OCR module mirrors MAX_DEMAND_QTY to stay free of database imports.

    Two copies of one ceiling drift; this is the check that keeps them equal.
    """
    from app.services.ingestion.image import MAX_PLAUSIBLE_DEMAND
    from app.services.ingestion.normaliser import MAX_DEMAND_QTY

    assert MAX_PLAUSIBLE_DEMAND == MAX_DEMAND_QTY


def test_remote_ocr_accepts_a_bare_json_array(monkeypatch: pytest.MonkeyPatch) -> None:
    """A service may answer with a list, not ``{"rows": [...]}``.

    The old one-liner called ``.get`` before checking the type, so an array body
    raised AttributeError, its rows were discarded, and the upload silently fell
    back to Tesseract — a correct remote read thrown away.
    """
    from app.services.ingestion import image as image_module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, object]]:
            return [{"date": "2026-01-12", "demand_qty": 112}]

    monkeypatch.setattr(image_module.httpx, "post", lambda *a, **k: _Response())
    rows = image_module._extract_via_remote(b"x", "log.jpg", "http://ocr.test")
    assert [row["demand_qty"] for row in rows] == [112]


def test_remote_ocr_keeps_a_zero_and_refuses_a_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.ingestion import image as image_module

    class _Response:
        def __init__(self, rows: list[dict[str, object]]) -> None:
            self._rows = rows

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"rows": self._rows}

    monkeypatch.setattr(
        image_module.httpx,
        "post",
        lambda *a, **k: _Response([{"date": "2026-01-12", "demand_qty": 0}]),
    )
    zero = image_module._extract_via_remote(b"x", "log.jpg", "http://ocr.test")
    assert [row["demand_qty"] for row in zero] == [0]

    monkeypatch.setattr(
        image_module.httpx,
        "post",
        lambda *a, **k: _Response([{"date": "2026-01-12", "demand_qty": -4}]),
    )
    assert image_module._extract_via_remote(b"x", "log.jpg", "http://ocr.test") == []


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
    note = preview["note"].lower()
    assert any(
        reason in note for reason in ("engine", "configured", "look like an image", "valid image")
    )

    # With Tesseract available the engine is reported as the local parser.
    monkeypatch = pytest.MonkeyPatch()
    try:
        from app.services.ingestion.image import ImageExtraction

        monkeypatch.setattr(
            "app.services.ingestion.image._tesseract_extraction",
            lambda content, language, **kwargs: ImageExtraction(
                rows=parse_ocr_text("12/01/2026   112"),
                engine=ENGINE_TESSERACT,
                quality={"page_segmentation_mode": 6, "mean_confidence": 0.91},
            ),
        )
        preview = preview_for_image(_ONE_PIXEL_PNG, "log.jpg")
        assert preview["engine"] == ENGINE_TESSERACT
        assert preview["row_count"] == 1
        assert preview["quality"]["mean_confidence"] == 0.91
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

    # Two bare numbers leave the count a choice: the nearest to the date was
    # taken for the operator — worth flagging before it is committed.
    ambiguous = parse_ocr_text("12/01/2026   112   6")[0]
    assert ambiguous["demand_qty"] == 112
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
