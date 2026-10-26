"""Measuring OCR extraction against a verified reference sheet.

Unit tests prove the parser does what it was told; they say nothing about whether
it reads a real photograph. Before spending more effort on recognition rules, the
pipeline has to be scored against pages a human has verified, so the next
improvement is chosen by a measured failure rather than a guess.

Reference format — one row per expected record:

    image,date,demand_qty,box
    oct-01.jpg,2026-10-01,112,
    oct-02.jpg,2026-10-02,87,"0.08,0.31,0.12,0.03"

``date`` is ISO. ``box`` is optional: the region of that date *and* its count as
``x,y,w,h`` fractions of the photo. Supplying it turns on highlight accuracy, so
a box can be checked against something other than "it exists".

Reported per page, then totalled:

* ``dates_recalled`` — expected dates that appear in the extraction at all.
* ``counts_matched`` — expected counts found anywhere, ignoring which date they
  were attached to. This separates "the recognizer misread the number" from "the
  parser attached a correct number to the wrong day".
* ``pairs_correct`` — date and count both right on the same row, which is the
  only number that describes what gets committed.
* ``missing`` / ``spurious`` / ``duplicated`` / ``misassociated`` — what went
  wrong, named, because each points at a different fix. A misassociation is a
  count attached to the wrong day in either direction: the date is right and its
  count is not, or the count was read correctly but sits on another day.
* ``rows_with_boxes`` — extracted rows carrying both highlight boxes, and, when
  the reference has boxes, ``box_matches`` at IoU >= 0.5 and the mean IoU.
* ``seconds`` — wall time per page, so a recognition upgrade can be judged
  against the review cost it adds.
"""

from __future__ import annotations

import csv
import datetime
import statistics
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.ingestion.image import preview_for_image

# A highlight covers the right place when it overlaps the reference box this
# much; below it, the operator is being pointed at the wrong part of the photo.
BOX_IOU_THRESHOLD = 0.5


@dataclass(frozen=True, slots=True)
class ReferenceRecord:
    """One record a human verified on a page."""

    date: datetime.date
    demand_qty: int
    # The date-and-count region, as fractions of the photo, when it was marked.
    box: tuple[float, float, float, float] | None = None


def _parse_box(raw: str) -> tuple[float, float, float, float]:
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) != 4:
        raise ValueError(f"box must be 'x,y,w,h', got {raw!r}")
    values = tuple(float(part) for part in parts)
    if any(value < 0 for value in values) or values[2] <= 0 or values[3] <= 0:
        raise ValueError(f"box must be positive in size and non-negative, got {raw!r}")
    return values  # type: ignore[return-value]


def load_reference(path: str | Path) -> dict[str, list[ReferenceRecord]]:
    """Read a reference sheet into ``{image filename: [records]}``.

    Raises:
        ValueError: Naming the row, the way a wrong count in a hand-made CSV
            should be reported — a benchmark that silently drops a row is worse
            than no benchmark, because the accuracy figure looks complete.
    """
    reference: dict[str, list[ReferenceRecord]] = {}
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = {"image", "date", "demand_qty"} - set(reader.fieldnames or ())
        if missing:
            raise ValueError(
                f"{path}: missing column(s) {', '.join(sorted(missing))}; "
                "expected image,date,demand_qty[,box]"
            )
        for line_number, row in enumerate(reader, start=2):
            try:
                date = datetime.date.fromisoformat((row["date"] or "").strip())
                demand_qty = int(row["demand_qty"])
                box = _parse_box(row["box"]) if (row.get("box") or "").strip() else None
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
            if demand_qty < 0:
                raise ValueError(f"{path}:{line_number}: demand_qty must be >= 0")
            name = (row["image"] or "").strip()
            if not name:
                raise ValueError(f"{path}:{line_number}: image is required")
            reference.setdefault(name, []).append(ReferenceRecord(date, demand_qty, box))
    return reference


def _iou(
    first: tuple[float, float, float, float], second: tuple[float, float, float, float]
) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[0] + first[2], second[0] + second[2])
    bottom = min(first[1] + first[3], second[1] + second[3])
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    union = first[2] * first[3] + second[2] * second[3] - intersection
    return intersection / union if union > 0 else 0.0


def _value_box(row: dict[str, Any]) -> tuple[float, float, float, float] | None:
    boxes = row.get("boxes") or {}
    raw = boxes.get("demand_qty") if isinstance(boxes, dict) else None
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    values = tuple(float(value) for value in raw)
    if values[2] <= 0 or values[3] <= 0:
        return None
    return values  # type: ignore[return-value]


def score_page(
    predicted: Iterable[dict[str, Any]], reference: Iterable[ReferenceRecord]
) -> dict[str, Any]:
    """Score one page's extraction against its verified records."""
    expected = list(reference)
    rows = list(predicted)

    expected_pairs = Counter((record.date.isoformat(), record.demand_qty) for record in expected)
    predicted_pairs = Counter(
        (str(row.get("date")), int(row.get("demand_qty") or 0)) for row in rows
    )
    pairs_correct = sum((expected_pairs & predicted_pairs).values())
    missing = sum((expected_pairs - predicted_pairs).values())
    spurious = sum((predicted_pairs - expected_pairs).values())

    # Counts ignoring their date: a correct figure on the wrong day is a layout
    # failure, not a recognition one, and the two are fixed in different places.
    expected_counts = Counter(record.demand_qty for record in expected)
    predicted_counts = Counter(int(row.get("demand_qty") or 0) for row in rows)
    counts_matched = sum((expected_counts & predicted_counts).values())

    truth_by_date: dict[str, Counter[int]] = {}
    for record in expected:
        truth_by_date.setdefault(record.date.isoformat(), Counter())[record.demand_qty] += 1
    predicted_by_date: dict[str, Counter[int]] = {}
    for row in rows:
        predicted_by_date.setdefault(str(row.get("date")), Counter())[
            int(row.get("demand_qty") or 0)
        ] += 1

    dates_recalled = len(set(truth_by_date) & set(predicted_by_date))
    rows_per_date = Counter(str(row.get("date")) for row in rows)
    duplicated = sum(count - 1 for count in rows_per_date.values() if count > 1)

    # A count attached to the wrong day, in either direction: the date is known
    # but its count is not one the sheet had, or the count is a real one read off
    # another day. Both mean the pairing is wrong even when the recognizer was
    # right, which is why they are counted apart from `missing` and `spurious`.
    count_dates: dict[int, set[str]] = {}
    for record in expected:
        count_dates.setdefault(record.demand_qty, set()).add(record.date.isoformat())
    misassociated = 0
    for row in rows:
        date = str(row.get("date"))
        demand_qty = int(row.get("demand_qty") or 0)
        if date in truth_by_date:
            if demand_qty not in truth_by_date[date]:
                misassociated += 1
        elif demand_qty in count_dates:
            misassociated += 1

    # Highlight accuracy: only measurable where the reference marked a box. The
    # predicted row is taken in order, so two rows for the same day cannot both
    # claim one reference box.
    remaining = list(rows)
    box_references = 0
    box_matches = 0
    ious: list[float] = []
    for record in expected:
        if record.box is None:
            continue
        index = next(
            (
                position
                for position, row in enumerate(remaining)
                if str(row.get("date")) == record.date.isoformat()
                and int(row.get("demand_qty") or 0) == record.demand_qty
            ),
            None,
        )
        if index is None:
            continue
        matched_row = remaining.pop(index)
        box_references += 1
        predicted_box = _value_box(matched_row)
        if predicted_box is None:
            continue
        iou = _iou(predicted_box, record.box)
        ious.append(iou)
        if iou >= BOX_IOU_THRESHOLD:
            box_matches += 1

    return {
        "expected": len(expected),
        "predicted": len(rows),
        "dates_expected": len(truth_by_date),
        "dates_recalled": dates_recalled,
        "counts_matched": counts_matched,
        "pairs_correct": pairs_correct,
        "missing": missing,
        "spurious": spurious,
        "duplicated": duplicated,
        "misassociated": misassociated,
        "rows_with_boxes": sum(1 for row in rows if _value_box(row) is not None),
        "box_references": box_references,
        "box_matches": box_matches,
        "box_iou_mean": round(statistics.fmean(ious), 4) if ious else None,
    }


def evaluate_directory(
    images_dir: str | Path,
    reference: dict[str, list[ReferenceRecord]],
    *,
    limit: int | None = None,
    reader: Callable[[bytes, str], dict[str, Any]] = preview_for_image,
) -> dict[str, Any]:
    """Run the real OCR path over every reference page and total the scores.

    ``reader`` is the production preview function by default; tests pass a stub so
    the scoring can be exercised without an engine.
    """
    directory = Path(images_dir)
    pages: list[dict[str, Any]] = []
    names = sorted(reference)[: limit if limit is not None else None]
    for name in names:
        path = directory / name
        if not path.exists():
            # Score the page as an empty extraction rather than skipping it, so a
            # missing photo reads as "these records were not recovered" and not
            # as a page that never existed.
            score = score_page([], reference[name])
            score.update(page=name, error="missing image file", seconds=0.0)
            pages.append(score)
            continue
        started = time.perf_counter()
        try:
            preview = reader(path.read_bytes(), name)
        except Exception as exc:  # a crash is a result, not the end of the run
            score = score_page([], reference[name])
            score.update(page=name, error=f"{exc.__class__.__name__}: {exc}", seconds=0.0)
            pages.append(score)
            continue
        seconds = round(time.perf_counter() - started, 3)
        score = score_page(preview.get("rows", []), reference[name])
        score.update(
            page=name,
            seconds=seconds,
            engine=preview.get("engine"),
            note=preview.get("note"),
        )
        pages.append(score)

    timed = [page["seconds"] for page in pages if page.get("expected") is not None]
    totals: dict[str, Any] = {
        "pages": len(pages),
        "failed_pages": sum(1 for page in pages if page.get("error")),
        "seconds_total": round(sum(page.get("seconds") or 0.0 for page in pages), 3),
        "seconds_median": round(statistics.median(timed), 3) if timed else None,
    }
    for field in (
        "expected",
        "predicted",
        "dates_expected",
        "dates_recalled",
        "counts_matched",
        "pairs_correct",
        "missing",
        "spurious",
        "duplicated",
        "misassociated",
        "rows_with_boxes",
        "box_references",
        "box_matches",
    ):
        totals[field] = sum(page.get(field) or 0 for page in pages)

    ious = [page["box_iou_mean"] for page in pages if page.get("box_iou_mean") is not None]
    totals["box_iou_mean"] = round(statistics.fmean(ious), 4) if ious else None
    totals["date_recall"] = (
        round(totals["dates_recalled"] / totals["dates_expected"], 4)
        if totals["dates_expected"]
        else None
    )
    totals["count_accuracy"] = (
        round(totals["counts_matched"] / totals["expected"], 4) if totals["expected"] else None
    )
    # The number that describes what would be committed: both fields right.
    totals["pair_accuracy"] = (
        round(totals["pairs_correct"] / totals["expected"], 4) if totals["expected"] else None
    )
    return {"pages": pages, "totals": totals}


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def format_report(report: dict[str, Any]) -> str:
    """Render an evaluation as text, per page and then in total."""
    lines: list[str] = []
    for page in report["pages"]:
        if page.get("error"):
            lines.append(f"{page['page']:<28} FAILED  {page['error']}")
            continue
        lines.append(
            f"{page['page']:<28} pairs {page['pairs_correct']}/{page['expected']}"
            f"  dates {page['dates_recalled']}/{page['dates_expected']}"
            f"  missing {page['missing']}  spurious {page['spurious']}"
            f"  dup {page['duplicated']}  misattributed {page['misassociated']}"
            f"  {page['seconds']:.2f}s  [{page.get('engine')}]"
        )
    totals = report["totals"]
    lines.append("")
    lines.append(
        f"{'TOTAL':<28} pairs {totals['pairs_correct']}/{totals['expected']}"
        f" ({_percent(totals['pair_accuracy'])})"
        f"  dates {totals['dates_recalled']}/{totals['dates_expected']}"
        f" ({_percent(totals['date_recall'])})"
        f"  counts {totals['counts_matched']}/{totals['expected']}"
        f" ({_percent(totals['count_accuracy'])})"
    )
    lines.append(
        f"{'':<28} missing {totals['missing']}  spurious {totals['spurious']}"
        f"  duplicated {totals['duplicated']}  misattributed {totals['misassociated']}"
    )
    lines.append(
        f"{'':<28} highlights {totals['rows_with_boxes']}/{totals['predicted']} rows"
        + (
            f"  box matches {totals['box_matches']}/{totals['box_references']}"
            f"  mean IoU {totals['box_iou_mean']}"
            if totals["box_references"]
            else ""
        )
    )
    lines.append(
        f"{'':<28} {totals['pages']} page(s), {totals['failed_pages']} failed,"
        f" {totals['seconds_total']}s total, median {totals['seconds_median']}s per page"
    )
    return "\n".join(lines)


def render_reference_page(records: Iterable[ReferenceRecord], path: str | Path) -> None:
    """Draw a clean log page, used to prove the harness itself works.

    A rendered page is printed rather than handwritten, so a perfect score here
    measures the plumbing wiring — not recognition. It is the check that stops a
    broken harness from being mistaken for a well-read photo.
    """
    from PIL import Image, ImageDraw, ImageFont

    items = list(records)
    width = 900
    line_height = 70
    image = Image.new("RGB", (width, 80 + line_height * max(1, len(items))), "white")
    draw = ImageDraw.Draw(image)
    font: Any
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    for index, record in enumerate(items):
        draw.text(
            (40, 40 + index * line_height),
            f"{record.date.strftime('%d/%m/%Y')}    {record.demand_qty}",
            fill="black",
            font=font,
        )
    image.save(str(path), format="PNG")


def write_self_check(directory: str | Path, *, pages: int = 2, records_per_page: int = 5) -> Path:
    """Write synthetic pages and their reference CSV; returns the CSV path."""
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    truth_path = folder / "truth.csv"
    with truth_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["image", "date", "demand_qty"])
        day = datetime.date(2026, 10, 1)
        for page in range(pages):
            records = []
            for index in range(records_per_page):
                date = day + datetime.timedelta(days=page * records_per_page + index)
                records.append(ReferenceRecord(date=date, demand_qty=40 + page * 10 + index))
            name = f"page-{page + 1}.png"
            render_reference_page(records, folder / name)
            for record in records:
                writer.writerow([name, record.date.isoformat(), record.demand_qty])
    return truth_path
