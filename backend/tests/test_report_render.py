"""Report rendering, the generation job and the signed download link.

The old report path was a stub: ``generate_report`` marked a row ``completed``
and stored a URI for a file nobody had produced. These tests pin what replaced
it — the renderer writes real bytes in all three formats, the worker is what
turns a queued row into a file, a failure is recorded on the row, and the
download link cannot be forged or reused after it expires.
"""

from __future__ import annotations

import datetime
import io
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.database import get_db
from app.deps import get_current_user
from app.jobs import runner
from app.main import create_app
from app.models.report import Report
from app.models.user import User, UserRole
from app.models.workspace import Workspace
from app.security.api_key_auth import get_principal
from app.services.reporting import builder
from app.services.reporting import render as render_mod
from app.services.reporting.render import Document, Section, render


def _document() -> Document:
    return Document(
        title="Demand summary",
        subtitle="Visitors per location, 2026-01-01 to 2026-01-31",
        summary=[("Period", "2026-01-01 → 2026-01-31"), ("Total visitors", "1,240")],
        sections=[
            Section(
                title="Visitors by location",
                columns=["Location", "Days recorded", "Total visitors"],
                rows=[["Remera Grills", "31", "980"], ["Kacyiru Cafe", "10", "260"]],
            )
        ],
        notes=["Synthetic figures for the renderer tests."],
    )


def _report(fmt: str = "pdf") -> Report:
    return Report(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        template="demand_summary",
        period_start=datetime.date(2026, 1, 1),
        period_end=datetime.date(2026, 1, 31),
        format=fmt,
        status="queued",
        created_at=datetime.datetime.now(datetime.UTC),
    )


class _FakeSession:
    """Just enough session for the generation job."""

    def __init__(self, reports: list[Report]) -> None:
        self.reports = reports
        self.flushes = 0

    def scalars(self, _stmt):  # type: ignore[no-untyped-def]
        class _Result:
            def all(self_inner) -> list[Report]:  # type: ignore[no-untyped-def]
                return [r for r in self.reports if r.status == "queued"]

        return _Result()

    def flush(self) -> None:
        self.flushes += 1

    def execute(self, _stmt):  # pragma: no cover - the renderer is patched out
        raise AssertionError("the job must not query once collection is patched")


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def test_csv_export_is_the_table_only() -> None:
    content = render(_document(), "csv")[0]
    text = content.decode("utf-8")
    lines = text.strip().splitlines()

    # Header first: a title row above it would make every export unparsable.
    assert lines[0] == "Location,Days recorded,Total visitors"
    assert lines[1] == "Remera Grills,31,980"
    assert len(lines) == 3
    assert "Demand summary" not in text


def test_xlsx_export_has_summary_and_a_section_sheet() -> None:
    content, content_type = render(_document(), "xlsx")
    assert content_type.startswith("application/vnd.openxmlformats")
    wb = load_workbook(io.BytesIO(content))
    assert wb.sheetnames == ["Summary", "Visitors by location"]

    summary = wb["Summary"]
    cells = [row for row in summary.iter_rows(values_only=True)]
    assert cells[0][0] == "Demand summary"
    assert ("Total visitors", "1,240") in cells

    sheet = wb["Visitors by location"]
    assert list(sheet.iter_rows(values_only=True))[0] == (
        "Location",
        "Days recorded",
        "Total visitors",
    )


def test_pdf_export_is_a_real_pdf() -> None:
    content, content_type = render(_document(), "pdf")
    assert content_type == "application/pdf"
    assert content.startswith(b"%PDF-")
    # A one-page PDF with three tables is comfortably more than 1 KB; an empty
    # header-only file would be a few hundred bytes.
    assert len(content) > 1000


def test_unknown_format_is_refused() -> None:
    with pytest.raises(ValueError):
        render(_document(), "docx")


def test_unknown_template_is_refused_before_any_query() -> None:
    # ``db=None`` is the point: the template check has to happen first, or a
    # typo would reach a session and fail somewhere less obvious.
    with pytest.raises(ValueError):
        render_mod.collect_document(None, workspace_id=uuid.uuid4(), template="nope")


# ---------------------------------------------------------------------------
# Generation job
# ---------------------------------------------------------------------------


def test_generation_job_renders_a_queued_report(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report("pdf")
    db = _FakeSession([report])
    monkeypatch.setattr(render_mod, "collect_document", lambda *a, **k: _document())

    outcome = runner._job_report_generation(db, report.workspace_id)  # type: ignore[arg-type]

    assert outcome.rows_processed == 1
    assert report.status == "completed"
    assert report.content is not None and report.content.startswith(b"%PDF-")
    assert report.content_type == "application/pdf"
    assert report.size_bytes == len(report.content)
    # The URI names its own storage: ``db://`` is not something an S3 client
    # could be pointed at by accident.
    assert report.file_uri == f"db://reports/{report.id}.pdf"
    assert report.error is None


def test_generation_job_records_a_failure_on_the_row(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report("csv")
    db = _FakeSession([report])

    def boom(*_a, **_k):  # type: ignore[no-untyped-def]
        raise ValueError("template exploded")

    monkeypatch.setattr(render_mod, "collect_document", boom)

    outcome = runner._job_report_generation(db, report.workspace_id)  # type: ignore[arg-type]

    assert outcome.rows_processed == 0
    assert "1 failed" in outcome.detail
    assert report.status == "failed"
    assert report.content is None
    # The reason has to outlive the job log, or the UI shows "failed" and nothing.
    assert report.error is not None and "template exploded" in report.error


def test_generation_job_is_idempotent_for_a_finished_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _report("csv")
    report.status = "completed"
    report.content = b"Location,Visitors\nA,1\n"
    report.size_bytes = len(report.content)
    db = _FakeSession([report])

    monkeypatch.setattr(
        render_mod,
        "collect_document",
        lambda *a, **k: pytest.fail("a completed report must not be re-rendered"),
    )

    outcome = runner._job_report_generation(db, report.workspace_id)  # type: ignore[arg-type]

    assert outcome.rows_processed == 0
    assert report.content == b"Location,Visitors\nA,1\n"


def test_queued_row_keeps_the_requested_location_filter() -> None:
    class _Collect:
        def __init__(self) -> None:
            self.added: list[Report] = []

        def add(self, obj: Report) -> None:
            self.added.append(obj)

        def flush(self) -> None:
            pass

    db = _Collect()
    loc = uuid.uuid4()
    report = builder.generate_report(
        db,  # type: ignore[arg-type]
        workspace_id=uuid.uuid4(),
        template_id="demand_summary",
        period_start=None,
        period_end=None,
        fmt="csv",
        location_ids=[loc],
    )
    # The worker is a separate pass: dropping the filter would render a
    # workspace-wide report for a request that asked for one location.
    assert report.location_ids == [str(loc)]
    assert report.status == "queued"
    assert db.added == [report]


def test_generation_hands_the_stored_filter_to_the_renderer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _report("csv")
    loc = uuid.uuid4()
    report.location_ids = [str(loc)]
    seen: dict[str, object] = {}

    def fake_collect(_db, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs)
        return _document()

    monkeypatch.setattr(render_mod, "collect_document", fake_collect)
    runner._job_report_generation(_FakeSession([report]), report.workspace_id)  # type: ignore[arg-type]

    assert seen["location_ids"] == [loc]


def test_generation_job_with_nothing_queued() -> None:
    db = _FakeSession([])
    outcome = runner._job_report_generation(db, uuid.uuid4())  # type: ignore[arg-type]
    assert outcome.rows_processed == 0
    assert "no queued" in outcome.detail


# ---------------------------------------------------------------------------
# Signed download link
# ---------------------------------------------------------------------------


def test_signed_url_is_absent_until_the_file_exists() -> None:
    report = _report("pdf")
    assert builder.build_signed_url(report) is None

    report.status = "completed"
    report.content = b"%PDF-1.4 fake"
    url = builder.build_signed_url(report)
    assert url is not None and url.startswith(f"/api/v1/reports/{report.id}/download?")
    assert "sig=" in url


def test_download_signature_is_bound_to_report_and_expiry() -> None:
    report_id = uuid.uuid4()
    expires = int(time.time()) + 60
    sig = builder._signature(report_id, expires)

    assert builder.verify_download_signature(report_id, expires, sig)
    assert not builder.verify_download_signature(uuid.uuid4(), expires, sig)
    assert not builder.verify_download_signature(report_id, expires, "0" * 32)

    past = int(time.time()) - 1
    expired = builder._signature(report_id, past)
    assert not builder.verify_download_signature(report_id, past, expired)


def _client(report: Report, monkeypatch_ws: Workspace | None = None):  # type: ignore[no-untyped-def]
    ws = monkeypatch_ws or Workspace(
        id=report.workspace_id,
        name="Test Co",
        business_type="cafe",
        country="RW",
        timezone="Africa/Kigali",
        created_at=datetime.datetime.now(datetime.UTC),
    )
    user = User(
        id=uuid.uuid4(),
        workspace_id=ws.id,
        name="Test User",
        email="test@example.com",
        password_hash="$2b$12$dummy",
        role=UserRole.owner,
        email_verified=True,
        created_at=datetime.datetime.now(datetime.UTC),
    )
    db = type(
        "DB", (), {"get": staticmethod(lambda model, pk: report if pk == report.id else None)}
    )()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db  # type: ignore[assignment]
    app.dependency_overrides[get_current_user] = lambda: user  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]
    return TestClient(app)


def test_download_serves_the_stored_bytes() -> None:
    report = _report("pdf")
    report.status = "completed"
    report.content = b"%PDF-1.4 stored bytes"
    report.content_type = "application/pdf"
    report.size_bytes = len(report.content)
    client = _client(report)

    resp = client.get(builder.build_signed_url(report))  # type: ignore[arg-type]

    assert resp.status_code == 200, resp.text
    assert resp.content == b"%PDF-1.4 stored bytes"
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment" in resp.headers["content-disposition"]
    client.app.dependency_overrides.clear()  # type: ignore[attr-defined]


def test_download_refuses_a_bad_signature_and_an_unready_report() -> None:
    report = _report("pdf")
    report.status = "completed"
    report.content = b"%PDF-1.4 stored bytes"
    client = _client(report)

    expires = int(time.time()) + 60
    bad = client.get(f"/api/v1/reports/{report.id}/download?expires={expires}&sig=deadbeef")
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == "invalid_signature"

    url = builder.build_signed_url(report)
    report.status = "queued"
    report.content = None
    unready = client.get(url)  # type: ignore[arg-type]
    assert unready.status_code == 409
    assert unready.json()["error"]["code"] == "not_ready"
    client.app.dependency_overrides.clear()  # type: ignore[attr-defined]
