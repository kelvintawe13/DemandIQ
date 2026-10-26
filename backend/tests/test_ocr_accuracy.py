"""Tests for the OCR accuracy benchmark.

The benchmark is what turns "the parser looks better" into a number, so its
arithmetic is pinned here: each failure mode it names (missing, spurious,
duplicated, misassociated) is counted separately, because each one points at a
different fix and conflating them would hide which.
"""

from __future__ import annotations

import datetime

import pytest

from app.services.ingestion.ocr_benchmark import (
    ReferenceRecord,
    evaluate_directory,
    format_report,
    load_reference,
    score_page,
)


def _record(day: int, qty: int, box: tuple[float, float, float, float] | None = None):
    return ReferenceRecord(datetime.date(2026, 10, day), qty, box)


def _row(day: int, qty: int, box: tuple[float, float, float, float] | None = None):
    return {
        "date": datetime.date(2026, 10, day).isoformat(),
        "demand_qty": qty,
        "boxes": {"date": None, "demand_qty": list(box) if box else None},
    }


def test_a_perfect_read_scores_everything() -> None:
    score = score_page(
        [_row(1, 112), _row(2, 87)],
        [_record(1, 112), _record(2, 87)],
    )
    assert score["pairs_correct"] == 2
    assert score["dates_recalled"] == 2
    assert score["counts_matched"] == 2
    assert (score["missing"], score["spurious"], score["duplicated"]) == (0, 0, 0)
    assert score["misassociated"] == 0


def test_a_count_on_the_wrong_day_is_not_a_correct_pair() -> None:
    """The figure was read right and attached to the wrong date.

    That is a layout failure, not a recognition one, so it has to show up as a
    misassociation while the count itself still counts as recognised — otherwise
    the two problems would be chased with the same fix.
    """
    score = score_page([_row(2, 112)], [_record(1, 112)])
    assert score["counts_matched"] == 1
    assert score["pairs_correct"] == 0
    assert score["misassociated"] == 1
    assert score["missing"] == 1
    assert score["spurious"] == 1


def test_a_repeated_date_is_counted_as_duplicated() -> None:
    score = score_page(
        [_row(1, 112), _row(1, 112), _row(2, 87)],
        [_record(1, 112), _record(2, 87)],
    )
    assert score["pairs_correct"] == 2
    assert score["duplicated"] == 1
    # The extra row matches no reference record.
    assert score["spurious"] == 1
    assert score["missing"] == 0


def test_a_missing_page_scores_as_missing_not_as_noise() -> None:
    score = score_page([], [_record(1, 112), _record(2, 87)])
    assert (score["pairs_correct"], score["missing"], score["spurious"]) == (0, 2, 0)
    assert score["dates_recalled"] == 0


def test_a_highlight_inside_the_reference_box_matches() -> None:
    score = score_page(
        [_row(1, 112, box=(0.10, 0.20, 0.20, 0.05))],
        [_record(1, 112, box=(0.10, 0.20, 0.20, 0.05))],
    )
    assert score["box_references"] == 1
    assert score["box_matches"] == 1
    assert score["box_iou_mean"] == 1.0
    assert score["rows_with_boxes"] == 1


def test_a_highlight_on_the_wrong_part_of_the_page_does_not_match() -> None:
    score = score_page(
        [_row(1, 112, box=(0.70, 0.80, 0.10, 0.04))],
        [_record(1, 112, box=(0.10, 0.20, 0.20, 0.05))],
    )
    assert score["box_references"] == 1
    assert score["box_matches"] == 0
    assert score["box_iou_mean"] == 0.0


def test_a_row_without_geometry_is_reported_as_unhighlighted() -> None:
    score = score_page([_row(1, 112)], [_record(1, 112, box=(0.1, 0.2, 0.2, 0.05))])
    assert score["rows_with_boxes"] == 0
    # A reference box with no prediction to compare is still a reference.
    assert score["box_references"] == 1
    assert score["box_matches"] == 0


def test_reference_csv_accepts_an_optional_box(tmp_path) -> None:
    truth = tmp_path / "truth.csv"
    truth.write_text(
        "image,date,demand_qty,box\n"
        "a.jpg,2026-10-01,112,\n"
        'a.jpg,2026-10-02,87,"0.1,0.2,0.2,0.05"\n',
        encoding="utf-8",
    )
    reference = load_reference(truth)
    assert [record.demand_qty for record in reference["a.jpg"]] == [112, 87]
    assert reference["a.jpg"][0].box is None
    assert reference["a.jpg"][1].box == (0.1, 0.2, 0.2, 0.05)


def test_a_bad_reference_row_names_its_line(tmp_path) -> None:
    """A hand-made CSV will have mistakes; silently dropping a row would make the
    accuracy figure look complete when it is not."""
    truth = tmp_path / "truth.csv"
    truth.write_text(
        "image,date,demand_qty\n" "a.jpg,2026-10-01,112\n" "a.jpg,31/10/2026,87\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"truth\.csv:3"):
        load_reference(truth)


def test_a_reference_without_the_required_columns_is_refused(tmp_path) -> None:
    truth = tmp_path / "truth.csv"
    truth.write_text("image,when,count\na.jpg,2026-10-01,112\n", encoding="utf-8")
    with pytest.raises(ValueError, match="demand_qty"):
        load_reference(truth)


def test_evaluate_totals_pages_and_reports_a_missing_file(tmp_path) -> None:
    (tmp_path / "a.png").write_bytes(b"png")
    reference = {
        "a.png": [_record(1, 112)],
        "b.png": [_record(2, 87)],
    }

    def reader(data: bytes, name: str) -> dict:
        return {"rows": [_row(1, 112)], "engine": "tesseract"}

    report = evaluate_directory(tmp_path, reference, reader=reader)
    totals = report["totals"]
    assert totals["pages"] == 2
    assert totals["failed_pages"] == 1
    assert totals["pairs_correct"] == 1
    assert totals["expected"] == 2
    assert totals["pair_accuracy"] == 0.5
    assert totals["date_recall"] == 0.5
    # The absent page is named, not quietly scored as a perfect empty read.
    assert any(page.get("error") for page in report["pages"])


def test_evaluate_measures_time_per_page(tmp_path) -> None:
    (tmp_path / "a.png").write_bytes(b"png")
    report = evaluate_directory(
        tmp_path,
        {"a.png": [_record(1, 112)]},
        reader=lambda data, name: {"rows": [_row(1, 112)]},
    )
    assert report["pages"][0]["seconds"] >= 0
    assert report["totals"]["seconds_median"] is not None


def test_report_names_each_failure_mode(tmp_path) -> None:
    (tmp_path / "a.png").write_bytes(b"png")
    report = evaluate_directory(
        tmp_path,
        {"a.png": [_record(1, 112)]},
        reader=lambda data, name: {"rows": [_row(2, 112), _row(2, 112)]},
    )
    text = format_report(report)
    assert "spurious" in text and "misattributed" in text and "duplicated" in text
    assert "pairs 0/1" in text


def test_the_harness_scores_a_rendered_page_it_knows_is_right(tmp_path) -> None:
    """End-to-end plumbing check with a real engine.

    The pages are printed, not handwritten: this asserts the harness measures a
    known-good page as good. It is the guard against a broken harness reporting
    zeroes that get blamed on recognition.
    """
    pytest.importorskip("PIL")
    pytesseract = pytest.importorskip("pytesseract")
    try:
        pytesseract.get_tesseract_version()
    except Exception as exc:  # pragma: no cover - host package dependent
        pytest.skip(f"Tesseract binary unavailable: {exc}")

    from app.services.ingestion.ocr_benchmark import write_self_check

    truth = write_self_check(tmp_path, pages=2, records_per_page=5)
    report = evaluate_directory(tmp_path, load_reference(truth))
    totals = report["totals"]

    assert totals["pages"] == 2
    assert totals["pair_accuracy"] == 1.0, format_report(report)
    assert totals["date_recall"] == 1.0
    assert totals["spurious"] == 0
    # And the rows carry geometry, which is what the review screen draws on.
    assert totals["rows_with_boxes"] == totals["predicted"] > 0
