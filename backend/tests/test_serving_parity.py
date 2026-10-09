"""Serving-contract tests: rsv features, online ACI, and training parity.

Three groups:

1. ``rsv_*`` feature serving — the reservation features must reproduce the
   training definition exactly (sum/count of pre-day bookings; ratio against
   the store's first non-null roll_mean_28, i.e. the mean of its first 7
   observed values).
2. Online-ACI state — seeding from the bundle, the state-aware half-width,
   and the Gibbs & Candès update math (mirrored from the training loop).
3. Full serving parity — backend ``build_model_features`` + exported booster
   must reproduce the training run's own test-window predictions, including
   reservation stores. Skipped when the run artifacts are absent (e.g. CI
   without the ``model/`` tree).
"""

from __future__ import annotations

import statistics
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import feature_build as fb
from app.services.forecasting import conformal as conf_mod
from app.services.forecasting.bundle import bundle_holidays

pytest.importorskip("lightgbm")
pytest.importorskip("pandas")
pytest.importorskip("numpy")

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "model" / "artifacts" / "runs" / "latest"
BUNDLE_PATH = RUN / "serving_bundle.joblib"
PP_PATH = RUN / "lightgbm_predictions.parquet"
VISITS_PATH = RUN / "visits.parquet"
FEATS_PATH = RUN / "features.parquet"
RSV_CSV = ROOT / "model" / "data" / "recruit" / "air_reserve.csv"

requires_artifacts = pytest.mark.skipif(
    not (
        BUNDLE_PATH.exists()
        and PP_PATH.exists()
        and VISITS_PATH.exists()
        and FEATS_PATH.exists()
        and RSV_CSV.exists()
    ),
    reason="training run artifacts not present",
)


class _FakeDB:
    """Minimal DB stand-in: no Location row, empty scalars, flush is a no-op."""

    def __init__(self, reservations=None) -> None:
        self._reservations = reservations or []
        self.added: list = []

    def get(self, model, key):
        return None

    def scalars(self, *a, **k):
        return MagicMock(all=MagicMock(return_value=list(self._reservations)))

    def scalar(self, *a, **k):
        return None

    def add(self, obj) -> None:
        self.added.append(obj)

    def flush(self) -> None:
        return None


# ---------------------------------------------------------------------------
# 1. rsv_* feature serving (training definition)
# ---------------------------------------------------------------------------


def _rsv_case(monkeypatch, reservations):
    monkeypatch.setattr(
        fb,
        "_history_for_location",
        lambda *a, **k: dict(
            zip(
                [date(2026, 9, d) for d in range(10, 20)],
                [10, 12, 9, 11, 13, 10, 14, 8, 15, 9],
            )
        ),
    )
    db = _FakeDB(reservations)
    return fb.build_model_features(
        db,
        location_id=uuid.uuid4(),
        target_date=date(2026, 9, 20),
        as_of=datetime(2026, 9, 19, 18, 0, tzinfo=UTC),
    )


def test_rsv_features_match_training_definition(monkeypatch):
    rows = [
        SimpleNamespace(reserved_visitors=10, reserved_count=2),
        SimpleNamespace(reserved_visitors=5, reserved_count=1),
    ]
    fv = _rsv_case(monkeypatch, rows)
    assert fv.features["rsv_visitors"] == 15.0
    assert fv.features["rsv_count"] == 3
    # Ratio denominator = store's first non-null roll_mean_28 in the training
    # frame = mean of its first 7 observed values.
    denom = statistics.fmean([10, 12, 9, 11, 13, 10, 14])
    assert fv.features["rsv_visitors_ratio"] == pytest.approx(15.0 / denom)


def test_rsv_features_stay_null_without_reservations(monkeypatch):
    fv = _rsv_case(monkeypatch, [])
    assert fv.features["rsv_visitors"] is None
    assert fv.features["rsv_count"] is None
    assert fv.features["rsv_visitors_ratio"] is None


# ---------------------------------------------------------------------------
# 2. Online-ACI state
# ---------------------------------------------------------------------------


def _mini_conf():
    residuals = [abs(float(v)) for v in __import__("numpy").random.default_rng(5).normal(0, 6, 60)]
    return {
        "target": 0.90,
        "gamma": 0.02,
        "window": 200,
        "min_history": 20,
        "min_bucket": 30,
        "bins": [[0.0, 15.0], [15.0, 30.0], [30.0, 60.0], [60.0, None]],
        "global": {"alpha": 0.10, "residuals": list(residuals)},
        "strata": [{"alpha": 0.10, "residuals": list(residuals), "scored": 10} for _ in range(4)],
    }


def test_state_from_bundle_seeds_live_state():
    st = conf_mod._state_from_bundle(_mini_conf())
    assert st["alphas"] == [0.10] * 4
    assert st["global_alpha"] == 0.10
    assert st["updates"] == 0
    assert len(st["residual_windows"]) == 4


def test_half_width_uses_live_state_override():
    import numpy as np

    conf = _mini_conf()
    st = conf_mod._state_from_bundle(conf)
    st["alphas"][1] = 0.05  # narrow bucket-1 only
    hw = conf_mod._half_width_from_state(conf, 20.0, state=st)
    res = np.asarray(st["residual_windows"][1], dtype=float)
    window = res[-conf["window"] :]
    q = float(np.quantile(window, 1 - 0.05, method="higher"))
    assert hw == pytest.approx(q * (1 + 1 / window.size))


def test_record_outcome_miss_and_cover(monkeypatch):
    conf = _mini_conf()
    monkeypatch.setattr(conf_mod, "get_bundle", lambda: {"conformal": conf})
    ws, loc, mv = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    target = date(2026, 9, 20)

    fx = SimpleNamespace(
        point_forecast=20.0,
        lower_bound=5.0,
        upper_bound=35.0,
        model_version_id=mv,
        created_at=datetime.now(UTC),
    )
    db = _FakeDB()
    db.scalar = MagicMock(side_effect=[fx, None])  # forecast, then calibration

    # MISS: actual 100 vs interval [5, 35] → alpha falls by gamma * target.
    out = conf_mod.record_conformal_outcome(
        db, workspace_id=ws, location_id=loc, target_date=target, actual=100.0
    )
    assert out is not None
    assert out["missed"] is True
    assert out["bucket"] == 1
    assert out["alpha_after"] == pytest.approx(0.10 + 0.02 * ((1 - 0.90) - 1.0))
    calib = db.added[0]
    st = calib.aci_state
    assert st["alphas"][1] == pytest.approx(0.082)
    assert st["global_alpha"] == pytest.approx(0.082)
    assert st["residual_windows"][1][-1] == pytest.approx(80.0)
    assert st["updates"] == 1

    # COVER: actual inside → alpha rises by gamma * (1 - target).
    db2 = _FakeDB()
    db2.scalar = MagicMock(side_effect=[fx, None])
    out2 = conf_mod.record_conformal_outcome(
        db2, workspace_id=ws, location_id=loc, target_date=target, actual=22.0
    )
    assert out2["missed"] is False
    assert out2["alpha_after"] == pytest.approx(0.10 + 0.02 * (1 - 0.90))


def test_record_outcome_returns_none_without_forecast(monkeypatch):
    monkeypatch.setattr(conf_mod, "get_bundle", lambda: {"conformal": _mini_conf()})
    db = _FakeDB()
    db.scalar = MagicMock(return_value=None)
    out = conf_mod.record_conformal_outcome(
        db,
        workspace_id=uuid.uuid4(),
        location_id=uuid.uuid4(),
        target_date=date(2026, 9, 20),
        actual=10.0,
    )
    assert out is None


# ---------------------------------------------------------------------------
# 3. Full serving parity vs the training run's own predictions
# ---------------------------------------------------------------------------


@requires_artifacts
def test_serving_matches_training_predictions(monkeypatch):
    import joblib
    import numpy as np
    import pandas as pd

    from app.services.feature_build import build_model_features
    from app.services.forecasting.loader import _point_from_bundle

    bundle = joblib.load(BUNDLE_PATH)
    cols = bundle["model"]["feature_columns"]
    holidays = bundle_holidays(bundle)
    assert holidays, "bundle must carry the training holiday calendar"

    visits = pd.read_parquet(VISITS_PATH)
    pp = pd.read_parquet(PP_PATH)
    feats = pd.read_parquet(FEATS_PATH)

    # Reservation aggregates under the training contract (pre-day bookings).
    rsv = pd.read_csv(RSV_CSV)
    rsv["visit_datetime"] = pd.to_datetime(rsv["visit_datetime"])
    rsv["reserve_datetime"] = pd.to_datetime(rsv["reserve_datetime"])
    keep = rsv[rsv.reserve_datetime < rsv.visit_datetime].copy()
    keep["visit_date"] = keep.visit_datetime.dt.date
    agg = keep.groupby(["air_store_id", "visit_date"], as_index=False).agg(
        reserved_visitors=("reserve_visitors", "sum"),
        reserved_count=("reserve_visitors", "size"),
    )
    agg_idx = {(r.air_store_id, r.visit_date): r for r in agg.itertuples()}

    # Deterministic picks: 2 stores without reservations + 1-2 with.
    fr = feats[feats.visit_date == pd.Timestamp("2017-03-15")]
    no_rsv = fr[fr.rsv_visitors.isna()]
    with_rsv = fr[fr.rsv_visitors.notna()]
    assert len(no_rsv) and len(with_rsv), "expected both rsv and no-rsv stores on the day"
    picks = [no_rsv.iloc[0], no_rsv.iloc[len(no_rsv) // 2], with_rsv.iloc[0]]
    if len(with_rsv) > 1:
        picks.append(with_rsv.iloc[-1])

    visits_by_store = {sid: g for sid, g in visits.groupby("air_store_id")}
    loc_map = {
        sid: uuid.uuid5(uuid.NAMESPACE_URL, f"diq:{sid}") for sid in {r.air_store_id for r in picks}
    }
    sid_of = {u: s for s, u in loc_map.items()}

    def fake_history(db, location_id, *, target_date, as_of):
        g = visits_by_store[sid_of[location_id]]
        h = g[g.visit_date < pd.Timestamp(target_date)]
        return dict(zip(h.visit_date.dt.date, h.visitors.astype(int)))

    def fake_reservations(db, location_id, *, target_date):
        row = agg_idx.get((sid_of[location_id], target_date))
        return [row] if row is not None else []

    monkeypatch.setattr(fb, "_history_for_location", fake_history)
    monkeypatch.setattr(fb, "_reservations_for_location", fake_reservations)

    pipe_map = {(r.air_store_id, r.visit_date.date()): float(r.prediction) for r in pp.itertuples()}

    for row in picks:
        sid = row.air_store_id
        tdate = row.visit_date.date()
        as_of = datetime.combine(
            tdate - __import__("datetime").timedelta(days=1),
            __import__("datetime").time(18, 0),
            tzinfo=UTC,
        )
        fv = build_model_features(
            _FakeDB(),
            location_id=loc_map[sid],
            target_date=tdate,
            as_of=as_of,
            holidays=holidays,
        )

        # Feature parity — all 26 columns, including rsv_* now.
        for c in cols:
            pv = getattr(row, c)
            bv = fv.features.get(c)
            pv = None if pv is None or (isinstance(pv, float) and np.isnan(pv)) else pv
            if pv is None or bv is None:
                assert pv is None and bv is None, f"{sid} {tdate} {c}: {pv!r} vs {bv!r}"
            else:
                assert abs(float(pv) - float(bv)) <= 1e-6, f"{sid} {tdate} {c}: {pv} vs {bv}"

        # Point parity vs the training run's own prediction.
        point = _point_from_bundle(bundle, fv.features)
        assert point is not None
        expected = pipe_map[(sid, row.visit_date.date())]
        assert abs(point - expected) <= 0.05, f"{sid} {tdate}: {point} vs {expected}"

        # Interval sanity.
        half = conf_mod._half_width_from_state(
            bundle["conformal"], point, state=conf_mod._state_from_bundle(bundle["conformal"])
        )
        assert half is not None
        assert max(0.0, point - half) <= point <= point + half
