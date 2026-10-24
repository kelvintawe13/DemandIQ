"""Report rendering — real files built from the workspace's own rows.

Three formats, one document model. ``collect_document`` reads the rows the
template is about and returns a :class:`Document`; ``render`` turns that document
into bytes for ``csv``, ``xlsx`` or ``pdf``. Keeping the two apart is what makes
the renderers testable without a database (feed them a document) and the data
selection testable without a file (assert on the document).

What each template reports, and where the numbers come from:

* ``demand_summary`` — ``demand_observations`` per location: days recorded,
  total visitors, mean per recorded day, peak day. No model, no estimates.
* ``forecast_accuracy`` — ``forecasts`` joined to realised
  ``demand_observations`` on ``(location_id, target_date)``: MAE, interval
  coverage and mean interval width, per location.
* ``waste_reduction`` — persisted ``policy_simulations`` rows only. Nothing is
  recomputed here and nothing is simulated for the report: if no simulation has
  been run for the period, the report says exactly that instead of printing
  zeros that look like results.
* ``data_quality_audit`` — ``ingest_uploads`` and demand coverage: days with a
  row against days in the period, plus what each upload produced. Duplicates are
  structurally impossible (``UNIQUE(location_id, date)``) and the report says so
  rather than claiming a check it cannot fail.

CSV carries the template's primary table and nothing else, so it stays
machine-parsable: no title rows, no totals footer. XLSX gets a ``Summary`` sheet
plus one sheet per section, and PDF gets the whole document.
"""

from __future__ import annotations

import csv
import datetime
import io
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.forecast import Forecast
from app.models.location import DemandObservation, IngestUpload, Location
from app.models.policy import PolicySimulation

#: Rows printed per section. A report is a summary, and a section that would run
#: to tens of thousands of lines is a sign the period or the filter is wrong.
MAX_SECTION_ROWS = 500

#: Days reported when a request carries no period.
DEFAULT_PERIOD_DAYS = 30

CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}


@dataclass(frozen=True, slots=True)
class Section:
    """One table in the document."""

    title: str
    columns: list[str]
    rows: list[list[str]]


@dataclass(frozen=True, slots=True)
class Document:
    """Everything a renderer needs, and nothing it has to fetch."""

    title: str
    subtitle: str
    summary: list[tuple[str, str]] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def primary(self) -> Section | None:
        """The table CSV exports — the first section, or nothing."""
        return self.sections[0] if self.sections else None


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _int(value: Any) -> str:
    if value is None:
        return "—"
    return f"{int(value):,}"


def _num(value: Any, places: int = 1) -> str:
    if value is None:
        return "—"
    if isinstance(value, Decimal):
        value = float(value)
    return f"{float(value):,.{places}f}"


def _pct(part: int, whole: int) -> str:
    if not whole:
        return "—"
    return f"{100.0 * part / whole:.1f}%"


def _period(
    period_start: datetime.date | None, period_end: datetime.date | None
) -> tuple[datetime.date, datetime.date]:
    """Resolve the period, defaulting to the last :data:`DEFAULT_PERIOD_DAYS`."""
    end = period_end or datetime.datetime.now(datetime.UTC).date()
    start = period_start or (end - datetime.timedelta(days=DEFAULT_PERIOD_DAYS - 1))
    if start > end:
        start, end = end, start
    return start, end


def _cap(rows: list[list[str]]) -> list[list[str]]:
    return rows[:MAX_SECTION_ROWS]


def _truncation_note(rows: list[list[str]], what: str, notes: list[str]) -> None:
    """Say when a table was cut, so a short report is never silently short."""
    if len(rows) > MAX_SECTION_ROWS:
        notes.append(
            f"{what} shows the first {MAX_SECTION_ROWS} of {len(rows)} rows; "
            "narrow the period or the location filter for the rest."
        )


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------


def _location_names(
    db: Session, workspace_id: uuid.UUID, location_ids: list[uuid.UUID] | None
) -> dict[uuid.UUID, str]:
    q = select(Location.id, Location.name).where(
        Location.workspace_id == workspace_id, Location.archived_at.is_(None)
    )
    if location_ids:
        q = q.where(Location.id.in_(location_ids))
    return {row[0]: row[1] for row in db.execute(q).all()}


def _demand_summary(
    db: Session,
    workspace_id: uuid.UUID,
    start: datetime.date,
    end: datetime.date,
    location_ids: list[uuid.UUID] | None,
) -> Document:
    names = _location_names(db, workspace_id, location_ids)
    q = (
        select(DemandObservation.location_id, DemandObservation.date, DemandObservation.demand_qty)
        .where(
            DemandObservation.workspace_id == workspace_id,
            DemandObservation.date >= start,
            DemandObservation.date <= end,
        )
        .order_by(DemandObservation.location_id, DemandObservation.date)
    )
    if location_ids:
        q = q.where(DemandObservation.location_id.in_(location_ids))

    per_location: dict[uuid.UUID, dict[str, Any]] = {}
    for loc_id, day, qty in db.execute(q).all():
        bucket = per_location.setdefault(
            loc_id, {"days": 0, "total": 0, "peak_day": None, "peak": None}
        )
        bucket["days"] += 1
        bucket["total"] += int(qty)
        if bucket["peak"] is None or qty > bucket["peak"]:
            bucket["peak"], bucket["peak_day"] = int(qty), day

    rows: list[list[str]] = []
    for loc_id, b in sorted(per_location.items(), key=lambda kv: names.get(kv[0], "")):
        mean = b["total"] / b["days"] if b["days"] else 0
        rows.append(
            [
                str(names.get(loc_id, loc_id)),
                _int(b["days"]),
                _int(b["total"]),
                _num(mean),
                b["peak_day"].isoformat() if b["peak_day"] else "—",
                _int(b["peak"]),
            ]
        )

    total = sum(b["total"] for b in per_location.values())
    days = sum(b["days"] for b in per_location.values())
    summary = [
        ("Period", f"{start.isoformat()} → {end.isoformat()}"),
        ("Locations with demand", _int(len(per_location))),
        ("Total visitors", _int(total)),
        ("Mean per recorded day", _num(total / days if days else 0)),
    ]
    notes: list[str] = []
    _truncation_note(rows, "Visitors by location", notes)
    if not rows:
        notes.append("No demand rows fall inside this period for these locations.")

    return Document(
        title="Demand summary",
        subtitle=f"Visitors per location, {start.isoformat()} to {end.isoformat()}",
        summary=summary,
        sections=[
            Section(
                title="Visitors by location",
                columns=[
                    "Location",
                    "Days recorded",
                    "Total visitors",
                    "Mean per day",
                    "Peak day",
                    "Peak visitors",
                ],
                rows=_cap(rows),
            )
        ],
        notes=notes,
    )


def _forecast_accuracy(
    db: Session,
    workspace_id: uuid.UUID,
    start: datetime.date,
    end: datetime.date,
    location_ids: list[uuid.UUID] | None,
) -> Document:
    names = _location_names(db, workspace_id, location_ids)
    q = (
        select(
            Forecast.location_id,
            Forecast.point_forecast,
            Forecast.lower_bound,
            Forecast.upper_bound,
            DemandObservation.demand_qty,
        )
        .join(
            DemandObservation,
            (DemandObservation.location_id == Forecast.location_id)
            & (DemandObservation.date == Forecast.target_date),
        )
        .where(
            Forecast.workspace_id == workspace_id,
            Forecast.target_date >= start,
            Forecast.target_date <= end,
            DemandObservation.workspace_id == workspace_id,
        )
    )
    if location_ids:
        q = q.where(Forecast.location_id.in_(location_ids))

    stats: dict[uuid.UUID, dict[str, float]] = {}
    for loc_id, point, lower, upper, actual in db.execute(q).all():
        b = stats.setdefault(loc_id, {"n": 0, "err": 0.0, "cover": 0, "width": 0.0})
        b["n"] += 1
        b["err"] += abs(float(point) - float(actual))
        b["cover"] += int(float(lower) <= float(actual) <= float(upper))
        b["width"] += float(upper) - float(lower)

    rows: list[list[str]] = []
    for loc_id, b in sorted(stats.items(), key=lambda kv: names.get(kv[0], "")):
        n = int(b["n"])
        rows.append(
            [
                str(names.get(loc_id, loc_id)),
                _int(n),
                _num(b["err"] / n if n else 0),
                _pct(int(b["cover"]), n),
                _num(b["width"] / n if n else 0),
            ]
        )

    scored = int(sum(b["n"] for b in stats.values()))
    mae = sum(b["err"] for b in stats.values()) / scored if scored else 0.0
    cover = sum(int(b["cover"]) for b in stats.values())
    notes: list[str] = []
    _truncation_note(rows, "Accuracy by location", notes)
    if not rows:
        notes.append(
            "No forecast has been scored in this period: a forecast is counted only "
            "once the day it targets has a realised demand row."
        )
    return Document(
        title="Forecast accuracy",
        subtitle=f"Predicted against realised demand, {start.isoformat()} to {end.isoformat()}",
        summary=[
            ("Period", f"{start.isoformat()} → {end.isoformat()}"),
            ("Forecast days scored", _int(scored)),
            ("MAE (visitors)", _num(mae)),
            ("Coverage against realised", _pct(cover, scored)),
        ],
        sections=[
            Section(
                title="Accuracy by location",
                columns=[
                    "Location",
                    "Days scored",
                    "MAE",
                    "Interval coverage",
                    "Mean interval width",
                ],
                rows=_cap(rows),
            )
        ],
        notes=notes,
    )


def _waste_reduction(
    db: Session,
    workspace_id: uuid.UUID,
    start: datetime.date,
    end: datetime.date,
    location_ids: list[uuid.UUID] | None,
) -> Document:
    names = _location_names(db, workspace_id, location_ids)
    q = (
        select(PolicySimulation)
        .where(
            PolicySimulation.workspace_id == workspace_id,
            PolicySimulation.period_end >= start,
            PolicySimulation.period_start <= end,
        )
        .order_by(PolicySimulation.simulated_at.desc())
    )
    if location_ids:
        q = q.where(PolicySimulation.location_id.in_(location_ids))

    rows: list[list[str]] = []
    prepared = surplus = unmet = 0.0
    for sim in db.scalars(q).all():
        prepared += float(sim.prepared_total)
        surplus += float(sim.surplus_ps)
        unmet += float(sim.unmet_ud)
        label = (
            str(names.get(sim.location_id, "All locations")) if sim.location_id else "All locations"
        )
        rows.append(
            [
                label,
                sim.period_start.isoformat(),
                sim.period_end.isoformat(),
                _num(sim.prepared_total, 0),
                _num(sim.surplus_sr, 0),
                _num(sim.surplus_ps, 0),
                _num(sim.unmet_ud, 0),
                _int(sim.surplus_days),
                _int(sim.unmet_days),
            ]
        )

    notes: list[str] = []
    _truncation_note(rows, "Simulated policies", notes)
    if not rows:
        notes.append(
            "No policy simulation covers this period. Run one in Policy lab; this "
            "report prints what was simulated and never fills the gap with zeros."
        )
    return Document(
        title="Waste reduction",
        subtitle=f"Prepared volume against simulated demand, {start.isoformat()} to {end.isoformat()}",
        summary=[
            ("Period", f"{start.isoformat()} → {end.isoformat()}"),
            ("Simulations included", _int(len(rows))),
            ("Prepared (portions)", _num(prepared, 0)),
            ("Surplus against what happened", _num(surplus, 0)),
            ("Unmet demand", _num(unmet, 0)),
        ],
        sections=[
            Section(
                title="Simulated policies",
                columns=[
                    "Location",
                    "From",
                    "To",
                    "Prepared",
                    "Surplus vs forecast",
                    "Surplus vs actual",
                    "Unmet demand",
                    "Surplus days",
                    "Unmet days",
                ],
                rows=_cap(rows),
            )
        ],
        notes=notes,
    )


def _data_quality_audit(
    db: Session,
    workspace_id: uuid.UUID,
    start: datetime.date,
    end: datetime.date,
    location_ids: list[uuid.UUID] | None,
) -> Document:
    names = _location_names(db, workspace_id, location_ids)
    days_in_period = (end - start).days + 1

    covered: dict[uuid.UUID, int] = {}
    q = (
        select(DemandObservation.location_id, DemandObservation.date)
        .where(
            DemandObservation.workspace_id == workspace_id,
            DemandObservation.date >= start,
            DemandObservation.date <= end,
        )
        .distinct()
    )
    if location_ids:
        q = q.where(DemandObservation.location_id.in_(location_ids))
    for loc_id, _day in db.execute(q).all():
        covered[loc_id] = covered.get(loc_id, 0) + 1

    coverage_rows: list[list[str]] = []
    for loc_id in sorted(names, key=lambda k: names[k]):
        have = covered.get(loc_id, 0)
        coverage_rows.append(
            [
                str(names[loc_id]),
                _int(have),
                _int(days_in_period),
                _int(max(0, days_in_period - have)),
                _pct(have, days_in_period),
            ]
        )

    upload_q = (
        select(IngestUpload)
        .where(
            IngestUpload.workspace_id == workspace_id,
            IngestUpload.created_at
            >= datetime.datetime.combine(start, datetime.time.min, datetime.UTC),
            # The period has an end too: an upload made after it belongs to the
            # next report, not this one.
            IngestUpload.created_at
            < datetime.datetime.combine(end, datetime.time.min, datetime.UTC)
            + datetime.timedelta(days=1),
        )
        .order_by(IngestUpload.created_at.desc())
    )
    uploads = [u for u in db.scalars(upload_q).all() if isinstance(u, IngestUpload)]

    upload_rows: list[list[str]] = []
    images = 0
    confidence: list[float] = []
    for up in uploads:
        preview = up.preview or {}
        quality = preview.get("quality") or {}
        mean_conf = quality.get("mean_confidence") if isinstance(quality, dict) else None
        if up.kind == "image":
            images += 1
            if isinstance(mean_conf, (int, float)):
                confidence.append(float(mean_conf))
        upload_rows.append(
            [
                up.created_at.date().isoformat(),
                up.kind,
                up.filename,
                up.status,
                _int(up.row_count),
                _int(up.error_count),
                _num(mean_conf, 2) if isinstance(mean_conf, (int, float)) else "—",
            ]
        )

    notes = [
        "Duplicate-day conflicts cannot occur: demand_observations holds "
        "UNIQUE(location_id, date), so a second write for a day reconciles the row "
        "that is already there.",
    ]
    _truncation_note(coverage_rows, "Demand coverage", notes)
    _truncation_note(upload_rows, "Uploads", notes)
    mean_read = sum(confidence) / len(confidence) if confidence else None
    if images and mean_read is None:
        notes.append(
            "Photo uploads in this period carry no engine confidence, so none is reported."
        )

    return Document(
        title="Data quality audit",
        subtitle=f"Ingestion health and coverage, {start.isoformat()} to {end.isoformat()}",
        summary=[
            ("Period", f"{start.isoformat()} → {end.isoformat()}"),
            ("Uploads in period", _int(len(uploads))),
            ("Photo uploads", _int(images)),
            ("Mean OCR confidence", _num(mean_read, 2) if mean_read is not None else "—"),
        ],
        sections=[
            Section(
                title="Demand coverage",
                columns=["Location", "Days with demand", "Days in period", "Gaps", "Coverage"],
                rows=_cap(coverage_rows),
            ),
            Section(
                title="Uploads",
                columns=[
                    "Date",
                    "Kind",
                    "File",
                    "Status",
                    "Rows",
                    "Errors",
                    "OCR confidence",
                ],
                rows=_cap(upload_rows),
            ),
        ],
        notes=notes,
    )


_COLLECTORS = {
    "demand_summary": _demand_summary,
    "forecast_accuracy": _forecast_accuracy,
    "waste_reduction": _waste_reduction,
    "data_quality_audit": _data_quality_audit,
}


def collect_document(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    template: str,
    period_start: datetime.date | None = None,
    period_end: datetime.date | None = None,
    location_ids: list[uuid.UUID] | None = None,
) -> Document:
    """Read the rows one report is about and return the document to render.

    Raises:
        ValueError: On an unknown template, before any query runs.
    """
    collector = _COLLECTORS.get(template)
    if collector is None:
        raise ValueError(f"Unknown report template '{template}'")
    start, end = _period(period_start, period_end)
    return collector(db, workspace_id, start, end, location_ids)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_csv(document: Document) -> bytes:
    """The primary table only — header row and data, nothing else.

    Metadata lives in the XLSX summary sheet and the PDF cover instead of as
    comment lines here, because a ``#`` prefix is not a comment in CSV and would
    turn every export into a file whose first row is not its header.
    """
    section = document.primary
    if section is None:
        raise ValueError("report has no table to export")
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(section.columns)
    writer.writerows(section.rows)
    return buffer.getvalue().encode("utf-8")


def _sheet_title(raw: str, used: set[str]) -> str:
    """Excel caps sheet names at 31 chars and forbids a few characters."""
    cleaned = "".join(c for c in raw if c not in "[]:*?/\\")[:28] or "Table"
    title = cleaned
    n = 2
    while title in used:
        suffix = f" ({n})"
        title = cleaned[: 31 - len(suffix)] + suffix
        n += 1
    used.add(title)
    return title


def render_xlsx(document: Document) -> bytes:
    """A ``Summary`` sheet plus one sheet per section."""
    from openpyxl import Workbook

    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    summary.append([document.title])
    summary.append([document.subtitle])
    summary.append([])
    summary.append(["Measure", "Value"])
    for label, value in document.summary:
        summary.append([label, value])
    for note in document.notes:
        summary.append([])
        summary.append([note])

    used: set[str] = {"Summary"}
    for section in document.sections:
        sheet = wb.create_sheet(_sheet_title(section.title, used))
        sheet.append(section.columns)
        for row in section.rows:
            sheet.append(row)
        sheet.freeze_panes = "A2"

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def render_pdf(document: Document) -> bytes:
    """A4 portrait, the summary as a two-column table, then one table per section."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "body", parent=styles["BodyText"], fontSize=8.5, leading=11, wordWrap="CJK"
    )
    heading = ParagraphStyle("heading", parent=styles["Heading2"], spaceBefore=10)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=10)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=document.title,
    )

    story: list[Any] = [
        Paragraph(document.title, styles["Title"]),
        Paragraph(document.subtitle, small),
        Spacer(1, 6 * mm),
    ]

    if document.summary:
        table = Table(
            [["Measure", "Value"]]
            + [[Paragraph(str(a), body), Paragraph(str(b), body)] for a, b in document.summary],
            colWidths=[70 * mm, 100 * mm],
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f8fafc")],
                    ),
                ]
            )
        )
        story.append(table)

    for section in document.sections:
        story.append(Paragraph(section.title, heading))
        header = [Paragraph(f"<b>{c}</b>", small) for c in section.columns]
        data = [header] + [[Paragraph(str(cell), small) for cell in row] for row in section.rows]
        width = (A4[0] - 36 * mm) / max(1, len(section.columns))
        table = Table(data, colWidths=[width] * len(section.columns), repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(table)
        if not section.rows:
            story.append(Paragraph("No rows in this period.", small))

    for note in document.notes:
        story.append(Spacer(1, 3 * mm))
        story.append(Paragraph(note, small))

    doc.build(story)
    return buffer.getvalue()


_RENDERERS = {
    "csv": render_csv,
    "xlsx": render_xlsx,
    "pdf": render_pdf,
}


def render(document: Document, fmt: str) -> tuple[bytes, str]:
    """Render a document, returning ``(bytes, content_type)``.

    Raises:
        ValueError: On an unsupported format.
    """
    key = (fmt or "").strip().lower()
    renderer = _RENDERERS.get(key)
    if renderer is None:
        raise ValueError(f"Unsupported report format '{fmt}'")
    content = renderer(document)
    if not content:
        # Every renderer writes at least a header; empty bytes mean a broken
        # writer, and storing them would mark a report complete with no file.
        raise ValueError("renderer produced an empty file")
    return content, CONTENT_TYPES[key]
