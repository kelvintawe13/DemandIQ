"""Hermetic unit tests for the connector pull adapter.

These run without a database: the provider call is stubbed and ``commit_demand``
is replaced with a recorder, so what is under test is the adapter's own rules —
which rows it commits, how it maps field names, and what it refuses to guess.
The real-Postgres half (rows actually landing in ``demand_observations``) lives
in ``tests/test_jobs_integration.py``.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from app.models.connector_mapping import ConnectorMapping
from app.models.location import Connector
from app.services.ingestion import connector_pull
from app.services.ingestion.normaliser import NormaliserError


class _Recorder:
    """A stand-in for ``commit_demand`` that records its calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, db: Any, **kwargs: Any) -> None:
        self.calls.append(kwargs)


def _connector(**overrides: Any) -> Connector:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "workspace_id": uuid.uuid4(),
        "type": "pos",
        "name": "Till",
        "status": "active",
        "credentials": {"endpoint": "https://pos.example/demand"},
        "last_sync_at": None,
        "created_at": datetime.datetime.now(datetime.UTC),
    }
    values.update(overrides)
    return Connector(**values)


def _stub(rows: Any):  # type: ignore[no-untyped-def]
    def _fetch(endpoint, headers, *, since=None, http_client=None):  # type: ignore[no-untyped-def]
        return rows

    return _fetch


def test_pull_commits_mapped_rows_and_tags_the_connector(monkeypatch) -> None:
    """Provider field names are translated and every row is committed with source_id."""
    conn = _connector()
    loc = uuid.uuid4()
    mappings = [
        ConnectorMapping(
            id=uuid.uuid4(), connector_id=conn.id, source_field="txn_date", canonical_field="date"
        ),
        ConnectorMapping(
            id=uuid.uuid4(),
            connector_id=conn.id,
            source_field="covers",
            canonical_field="demand_qty",
            transform="int",
        ),
        ConnectorMapping(
            id=uuid.uuid4(),
            connector_id=conn.id,
            source_field="outlet",
            canonical_field="location_id",
        ),
    ]
    day = datetime.date(2026, 3, 1)
    monkeypatch.setattr(
        connector_pull,
        "_fetch_payload",
        _stub([{"txn_date": day.isoformat(), "covers": "62", "outlet": str(loc)}]),
    )
    recorder = _Recorder()
    monkeypatch.setattr(connector_pull, "commit_demand", recorder)

    outcome = connector_pull.pull_connector(None, conn, mappings=mappings)

    assert outcome.configured is True
    assert (outcome.pulled, outcome.committed, outcome.skipped) == (1, 1, 0)
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["workspace_id"] == conn.workspace_id
    assert call["location_id"] == loc
    assert call["date"] == day
    assert call["demand_qty"] == 62
    assert call["source"] == "pos"
    assert call["source_id"] == conn.id


def test_pull_accepts_canonical_keys_without_a_mapping(monkeypatch) -> None:
    """With no mapping, canonical field names are read straight off the row."""
    conn = _connector()
    loc = uuid.uuid4()
    day = datetime.date(2026, 3, 2)
    monkeypatch.setattr(
        connector_pull,
        "_fetch_payload",
        _stub([{"date": day.isoformat(), "demand_qty": 40, "location_id": str(loc)}]),
    )
    recorder = _Recorder()
    monkeypatch.setattr(connector_pull, "commit_demand", recorder)

    outcome = connector_pull.pull_connector(None, conn, mappings=[])

    assert outcome.committed == 1
    assert recorder.calls[0]["location_id"] == loc
    assert recorder.calls[0]["date"] == day


def test_pull_skips_rows_it_cannot_commit_without_inventing_them(monkeypatch) -> None:
    """A row missing a required field is skipped and named, never defaulted."""
    conn = _connector()
    loc = uuid.uuid4()
    day = datetime.date(2026, 3, 3)
    monkeypatch.setattr(
        connector_pull,
        "_fetch_payload",
        _stub(
            [
                {"date": day.isoformat(), "demand_qty": 30, "location_id": str(loc)},
                {"date": day.isoformat(), "location_id": str(loc)},  # no demand_qty
                {"demand_qty": 12, "location_id": str(loc)},  # no date
            ]
        ),
    )
    recorder = _Recorder()
    monkeypatch.setattr(connector_pull, "commit_demand", recorder)

    outcome = connector_pull.pull_connector(None, conn, mappings=[])

    assert (outcome.pulled, outcome.committed, outcome.skipped) == (3, 1, 2)
    assert len(recorder.calls) == 1
    assert "no date" in " ".join(outcome.errors)
    assert "no demand_qty" in " ".join(outcome.errors)


def test_pull_without_an_endpoint_fetches_nothing(monkeypatch) -> None:
    """No endpoint means no fetch, no commit, and ``configured=False``."""
    conn = _connector(credentials=None)

    def _boom(*args: Any, **kwargs: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("fetch must not be called without an endpoint")

    monkeypatch.setattr(connector_pull, "_fetch_payload", _boom)
    recorder = _Recorder()
    monkeypatch.setattr(connector_pull, "commit_demand", recorder)

    outcome = connector_pull.pull_connector(None, conn, mappings=[])

    assert outcome.configured is False
    assert outcome.committed == 0
    assert "no pull endpoint" in outcome.detail
    assert recorder.calls == []


def test_pull_records_a_normaliser_refusal_as_skipped(monkeypatch) -> None:
    """A row rejected by the commit rules (e.g. a future date) is skipped, not fatal."""
    conn = _connector()
    loc = uuid.uuid4()
    monkeypatch.setattr(
        connector_pull,
        "_fetch_payload",
        _stub([{"date": "2099-01-01", "demand_qty": 10, "location_id": str(loc)}]),
    )

    def _reject(db: Any, **kwargs: Any) -> None:
        raise NormaliserError("future_date", "date is in the future", field="date")

    monkeypatch.setattr(connector_pull, "commit_demand", _reject)

    outcome = connector_pull.pull_connector(None, conn, mappings=[])

    assert outcome.committed == 0
    assert outcome.skipped == 1
    assert "future" in outcome.errors[0]


def test_dry_run_maps_rows_without_committing(monkeypatch) -> None:
    """A dry run returns the mapped rows but writes nothing."""
    conn = _connector()
    loc = uuid.uuid4()
    day = datetime.date(2026, 3, 4)
    monkeypatch.setattr(
        connector_pull,
        "_fetch_payload",
        _stub([{"date": day.isoformat(), "demand_qty": 12, "location_id": str(loc)}]),
    )
    checked: list[dict[str, Any]] = []
    monkeypatch.setattr(
        connector_pull,
        "validate_demand_row",
        lambda *args, **kwargs: checked.append(kwargs) or object(),
    )

    def _no_commit(*args: Any, **kwargs: Any) -> None:  # pragma: no cover - must not run
        raise AssertionError("a dry run must not commit")

    monkeypatch.setattr(connector_pull, "commit_demand", _no_commit)

    outcome = connector_pull.pull_connector(None, conn, mappings=[], dry_run=True)

    assert outcome.configured is True
    assert (outcome.pulled, outcome.committed, outcome.skipped) == (1, 0, 0)
    assert outcome.validated == 1
    assert outcome.rows[0]["date"] == day
    assert "preview" in outcome.detail
    assert len(checked) == 1  # the same rules the real commit applies


def test_dry_run_reports_a_row_the_rules_would_reject(monkeypatch) -> None:
    """A row the commit rules would refuse is shown as skipped, not as ready."""
    conn = _connector()
    loc = uuid.uuid4()
    monkeypatch.setattr(
        connector_pull,
        "_fetch_payload",
        _stub([{"date": "2099-01-01", "demand_qty": 10, "location_id": str(loc)}]),
    )

    def _reject(*args: Any, **kwargs: Any) -> None:
        raise NormaliserError("future_date", "date is in the future", field="date")

    monkeypatch.setattr(connector_pull, "validate_demand_row", _reject)

    outcome = connector_pull.pull_connector(None, conn, mappings=[], dry_run=True)

    assert (outcome.pulled, outcome.committed, outcome.validated, outcome.skipped) == (1, 0, 0, 1)
    assert "future" in outcome.errors[0]


def test_rows_from_payload_accepts_the_common_envelopes() -> None:
    assert connector_pull._rows_from_payload([{"a": 1}]) == [{"a": 1}]
    assert connector_pull._rows_from_payload({"rows": [{"a": 1}]}) == [{"a": 1}]
    assert connector_pull._rows_from_payload({"data": [{"a": 1}, "not-a-row"]}) == [{"a": 1}]
    assert connector_pull._rows_from_payload({"unexpected": []}) == []


def test_headers_prefer_an_explicit_auth_header_then_a_token() -> None:
    explicit = _connector(credentials={"endpoint": "x", "auth_header": "Token abc"})
    assert connector_pull._headers_for(explicit)["Authorization"] == "Token abc"

    tokenised = _connector(credentials={"endpoint": "x", "api_key": "secret"})
    assert connector_pull._headers_for(tokenised)["Authorization"] == "Bearer secret"

    plain = _connector(credentials={"endpoint": "x"})
    assert "Authorization" not in connector_pull._headers_for(plain)
