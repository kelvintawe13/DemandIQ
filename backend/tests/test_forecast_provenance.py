"""Forecast provenance: who produced the number, and is that recorded?

Two defects are pinned here.

1. ``forecasts.fallback`` was not stored. ``GET /forecasts`` hardcoded
   ``fallback: false`` on every row and ``GET /forecasts/{id}`` recomputed it
   from *today's* feature vector against the row's historical
   ``as_of_timestamp``. The same forecast therefore reported two different
   answers, and the list — the only endpoint the UI reads — was the wrong one.

2. The degraded serving paths (too little history, or no usable bundle) return a
   historical average but filed it against the workspace's *trained* model
   version, so ``forecasts.model_version_id`` credited LightGBM with a rolling
   mean.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import create_app
from app.models.forecast import Forecast, ModelVersion
from app.security.api_key_auth import get_principal
from app.services.feature_build import FeatureVector
from app.services.forecasting import loader as loader_mod
from app.services.forecasting.persist import persist_forecast


class _FakeDb:
    """Minimal Session double.

    ``scalar`` answers the two ``model_versions`` lookups the loader makes:
    the production lookup filters on ``status``, the fallback lookup on
    ``version``. Telling them apart by bind name is what a real database does
    with the same statement.
    """

    def __init__(
        self,
        *,
        production: ModelVersion | None = None,
        history: list[SimpleNamespace] | None = None,
    ) -> None:
        self.production = production
        self.history = history or []
        self.added: list = []
        self.flushes = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        self.flushes += 1

    def scalar(self, stmt: object) -> object | None:
        text = str(stmt)
        if "model_versions" not in text:
            return None
        if ":status_" in text:
            return self.production
        return None  # per-version fallback lookup: nothing registered yet

    def scalars(self, stmt: object) -> MagicMock:
        return MagicMock(all=MagicMock(return_value=list(self.history)))


def _feature_vector(loc: uuid.UUID, target: date, as_of: datetime, **kw: object) -> FeatureVector:
    return FeatureVector(location_id=loc, target_date=target, as_of=as_of, **kw)  # type: ignore[arg-type]


def _production_model(ws: uuid.UUID) -> ModelVersion:
    return ModelVersion(
        id=uuid.uuid4(),
        workspace_id=ws,
        algorithm="lightgbm",
        version="lightgbm-fs-2026.09.2-it200",
        status="production",
        feature_set_version="fs-2026.09.2",
        trained_at=datetime.now(UTC),
        promoted_at=datetime.now(UTC),
    )


# --------------------------------------------------------------------------- #
# Attribution
# --------------------------------------------------------------------------- #


def test_stub_forecast_is_filed_against_the_fallback_version(
    monkeypatch: object,
) -> None:
    """A rolling mean must not be filed against the trained model's version."""
    ws, loc = uuid.uuid4(), uuid.uuid4()
    target = date(2026, 9, 21)
    as_of = datetime(2026, 9, 20, 18, 0, tzinfo=UTC)
    production = _production_model(ws)
    db = _FakeDb(production=production)
    fv = _feature_vector(
        loc,
        target,
        as_of,
        features={"roll_mean_7": 30.0, "roll_mean_28": 28.0},
        history_days=40,
    )
    monkeypatch.setattr(loader_mod, "build_features", lambda *a, **k: fv)  # type: ignore[attr-defined]

    res = loader_mod.predict_point(
        db, workspace_id=ws, location_id=loc, target_date=target, as_of=as_of
    )

    assert res.model_version.version == loader_mod.STUB_MODEL_VERSION
    assert res.model_version.algorithm == "historical_avg"
    assert res.model_version.status == "fallback"
    assert res.model_version.id != production.id, "the trained version was credited with a mean"
    assert res.artifact.algorithm == "historical_avg"
    # The flag means "history-limited average", which this is not: enough history
    # existed, there was simply no model to score it with.
    assert res.fallback is False


def test_limited_history_forecast_is_filed_against_the_fallback_version(
    monkeypatch: object,
) -> None:
    """The <7-day average is a historical average too, and is filed as one."""
    ws, loc = uuid.uuid4(), uuid.uuid4()
    target = date(2026, 9, 21)
    as_of = datetime(2026, 9, 20, 18, 0, tzinfo=UTC)
    production = _production_model(ws)
    db = _FakeDb(
        production=production,
        history=[SimpleNamespace(demand_qty=q, recorded_at=None) for q in (40.0, 41.0, 42.0)],
    )
    fv = _feature_vector(loc, target, as_of, features={}, history_days=3)
    monkeypatch.setattr(loader_mod, "build_features", lambda *a, **k: fv)  # type: ignore[attr-defined]

    res = loader_mod.predict_point(
        db, workspace_id=ws, location_id=loc, target_date=target, as_of=as_of
    )

    assert res.fallback is True
    assert res.point == 41.0  # the real average of the three rows
    assert res.model_version.version == loader_mod.STUB_MODEL_VERSION
    assert res.model_version.id != production.id


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def test_persist_forecast_records_the_flag() -> None:
    """The flag travels with the row, so every read path can agree on it."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = MagicMock()
    row = persist_forecast(
        db,
        workspace_id=uuid.uuid4(),
        location_id=uuid.uuid4(),
        target_date=date(2026, 9, 21),
        point=41.0,
        lower=35.0,
        upper=47.0,
        coverage_target=0.9,
        model_version_id=uuid.uuid4(),
        feature_set_version="v1",
        as_of_timestamp=datetime(2026, 9, 20, 18, 0, tzinfo=UTC),
        fallback=True,
    )
    assert row.fallback is True


def _forecast_row(ws: uuid.UUID, *, fallback: bool) -> Forecast:
    return Forecast(
        id=uuid.uuid4(),
        workspace_id=ws,
        location_id=uuid.uuid4(),
        model_version_id=uuid.uuid4(),
        target_date=date(2026, 9, 21),
        point_forecast=41.0,
        lower_bound=35.0,
        upper_bound=47.0,
        coverage_target=90.0,
        feature_set_version="v1",
        as_of_timestamp=datetime(2026, 9, 20, 18, 0, tzinfo=UTC),
        fallback=fallback,
        created_at=datetime.now(UTC),
    )


def _client_with(row: Forecast, ws: uuid.UUID) -> TestClient:
    """A TestClient whose DB returns ``row`` and whose principal is an owner."""
    user = SimpleNamespace(id=uuid.uuid4(), workspace_id=ws)
    db = MagicMock()
    db.scalars.return_value = MagicMock(all=MagicMock(return_value=[row]))
    db.scalar.return_value = None
    db.get.return_value = row
    db.add = MagicMock()
    db.flush = MagicMock()

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db  # type: ignore[assignment]
    app.dependency_overrides[get_principal] = lambda: user  # type: ignore[assignment]
    return TestClient(app)


def test_forecast_list_reports_the_stored_fallback_flag() -> None:
    """The list used to hardcode ``false``; it now reads the row."""
    ws = uuid.uuid4()
    client = _client_with(_forecast_row(ws, fallback=True), ws)
    body = client.get("/api/v1/forecasts").json()
    assert body["data"][0]["fallback"] is True


def test_forecast_list_reports_false_for_a_model_forecast() -> None:
    """...and it is not simply always true."""
    ws = uuid.uuid4()
    client = _client_with(_forecast_row(ws, fallback=False), ws)
    body = client.get("/api/v1/forecasts").json()
    assert body["data"][0]["fallback"] is False


def test_forecast_detail_reads_the_flag_without_rebuilding_features(monkeypatch: object) -> None:
    """The detail path must not re-derive the flag from present-day history.

    Recomputing asked today's demand history to explain a forecast made at the
    row's ``as_of_timestamp``, so a row written as a fallback could read back as
    a model forecast. Any feature rebuild here is the bug, so this makes one
    impossible.
    """
    from app.routers import forecasts as router_mod

    def _boom(*a: object, **k: object) -> None:
        raise AssertionError("the flag must come from the row, not from a feature rebuild")

    ws = uuid.uuid4()
    row = _forecast_row(ws, fallback=True)
    monkeypatch.setattr(router_mod, "build_features", _boom)  # type: ignore[attr-defined]
    client = _client_with(row, ws)

    resp = client.get(f"/api/v1/forecasts/{row.id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["fallback"] is True


def test_forecast_model_has_the_fallback_column() -> None:
    """The ORM column exists (a migration without it would fail at insert)."""
    assert "fallback" in Forecast.__table__.columns
    assert Forecast.__table__.columns["fallback"].nullable is False
