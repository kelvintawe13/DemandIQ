"""Attribution paths: the served booster's TreeSHAP, and the labelled stub.

Finding I in ``docs/RETRAIN_GUIDE.md``: the product-side "SHAP" was
``sum(ord(c) for c in name) % 100`` — a function of the feature *name*, with no
model behind it — while the page presented the result as the forecast's
attribution. These tests hold the two states apart:

* with a serving bundle the rows are the booster's own exact ``pred_contrib``
  values for the vector the point was scored from, restated in demand units;
* without one there is no model to attribute to, so the rows are the labelled
  stub — and the arithmetic still adds up, because a placeholder that also
  mis-states the total would mislead twice.

The booster here is trained in the test rather than mocked: a fake returning
hand-picked numbers would not prove that the wiring reaches LightGBM, which is
the whole failure being pinned.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest

from app.models.forecast import ATTRIBUTION_BOOSTER, ATTRIBUTION_STUB, ForecastExplanation
from app.services.explain import shap as shap_mod
from app.services.feature_build import FeatureVector

pytest.importorskip("lightgbm")
pytest.importorskip("pandas")
pytest.importorskip("numpy")

COLUMNS = ["lag_1", "roll_mean_7", "roll_mean_28", "is_weekend"]


def _trained_bundle():
    """A real (tiny) LightGBM booster, wrapped as the serving bundle holds it."""
    import lightgbm as lgb
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(7)
    n = 400
    frame = pd.DataFrame(
        {
            "lag_1": rng.normal(40, 5, n),
            "roll_mean_7": rng.normal(40, 4, n),
            "roll_mean_28": rng.normal(40, 3, n),
            "is_weekend": rng.integers(0, 2, n).astype(float),
        }
    )
    # log1p target, matching the exported bundle's ``log_target`` booster.
    label = np.log1p(
        np.maximum(
            0.0,
            30 + 0.7 * frame["lag_1"] + 6.0 * frame["is_weekend"] - 0.3 * frame["roll_mean_28"],
        )
    )
    booster = lgb.train(
        {
            "objective": "regression",
            "num_leaves": 7,
            "min_data_in_leaf": 5,
            "learning_rate": 0.2,
            "verbose": -1,
            "seed": 7,
            "deterministic": True,
            "num_threads": 1,
        },
        lgb.Dataset(frame, label=label),
        num_boost_round=12,
    )
    bundle = {
        "format": "demandiq-serving-bundle",
        "bundle_version": 1,
        "model": {
            "algorithm": "lightgbm",
            "version": "test-it12",
            "feature_set_version": "fs-test",
            "feature_columns": list(COLUMNS),
            "booster": booster,
            "best_iteration": None,
            "log_target": True,
        },
    }
    return bundle, frame


def _row_vector(features: dict) -> FeatureVector:
    return FeatureVector(
        location_id=uuid.uuid4(),
        target_date=date(2026, 10, 11),
        as_of=datetime(2026, 10, 10, 3, 0, tzinfo=UTC),
        features=features,
        history_days=60,
    )


def _collecting_db(prior: list[object] | None = None) -> tuple[MagicMock, list]:
    """Session double that records the rows the writer adds."""
    db = MagicMock()
    added: list = []
    db.scalars.return_value = MagicMock(all=MagicMock(return_value=list(prior or [])))
    db.add.side_effect = added.append
    return db, added


def _split(rows: list) -> tuple[list, object | None]:
    named = [r for r in rows if r.feature_name != shap_mod.RESIDUAL_FEATURE]
    residual = next((r for r in rows if r.feature_name == shap_mod.RESIDUAL_FEATURE), None)
    return named, residual


# Stored rows keep 3 decimals, so a row can sit half a unit in the last place
# from its exact value, and the residual — which is computed from the rounded
# drivers — absorbs one such half-unit per driver plus its own.
_ROW_EPS = 5e-4


def _total_eps(rows: list) -> float:
    return _ROW_EPS * (len(rows) + 1)


# ------------------------------------------------------- the served booster


def test_served_booster_provides_the_attribution(monkeypatch) -> None:
    """The rows are the booster's own contributions, not a name hash."""
    import pandas as pd

    bundle, frame = _trained_bundle()
    booster = bundle["model"]["booster"]
    monkeypatch.setattr(shap_mod, "get_bundle", lambda: bundle)

    features = {c: float(frame.iloc[0][c]) for c in COLUMNS}
    X = pd.DataFrame([features])[COLUMNS]
    raw = booster.predict(X, num_iteration=None).ravel()
    point = math.expm1(float(raw[0]))
    fv = _row_vector(features)

    db, added = _collecting_db()
    out = shap_mod.build_explanation(db, forecast_id=uuid.uuid4(), feature_vector=fv, point=point)
    named, residual = _split(out)

    # One row per trained column, in the model's own order — and nothing else.
    assert [r.feature_name for r in named] == COLUMNS
    assert [r.feature_value for r in named] == [str(features[c]) for c in COLUMNS]
    assert residual is not None

    # Independently: LightGBM's exact TreeSHAP for the same row, restated in
    # demand units by splitting the movement from the base to the point.
    contribs = booster.predict(X, num_iteration=None, pred_contrib=True).ravel()
    base = math.expm1(float(contribs[-1]))
    shares = contribs[:-1]
    movement = point - base
    expected = [movement * float(s) / float(shares.sum()) for s in shares]

    for row, want in zip(named, expected, strict=True):
        assert float(row.shap_impact) == pytest.approx(want, abs=_ROW_EPS)
    assert float(residual.shap_impact) == pytest.approx(base, abs=_total_eps(named))

    # Base + drivers is the point being explained, to the rounding of storage.
    assert sum(float(r.shap_impact) for r in out) == pytest.approx(point, abs=_total_eps(named))

    # Every row records that a model produced it — the values alone cannot say
    # so, which is the whole reason the column exists.
    assert {r.attribution_source for r in out} == {ATTRIBUTION_BOOSTER}
    assert shap_mod.attribution_source_of(out) == ATTRIBUTION_BOOSTER

    # And none of it is the placeholder: the stub would answer by feature name.
    stub = [shap_mod._stub_impact_for_feature(c, features[c], point) for c in COLUMNS]
    assert [float(r.shap_impact) for r in named] != pytest.approx(stub, abs=1e-6)


def test_booster_contributions_keep_their_sign_and_ranking(monkeypatch) -> None:
    """The restatement rescales; it must not reorder or flip the model's story."""
    import pandas as pd

    bundle, frame = _trained_bundle()
    booster = bundle["model"]["booster"]
    monkeypatch.setattr(shap_mod, "get_bundle", lambda: bundle)

    for idx in (0, 3, 11):
        features = {c: float(frame.iloc[idx][c]) for c in COLUMNS}
        X = pd.DataFrame([features])[COLUMNS]
        point = math.expm1(float(booster.predict(X, num_iteration=None).ravel()[0]))
        contribs = booster.predict(X, num_iteration=None, pred_contrib=True).ravel()[:-1]
        fv = _row_vector(features)
        db, _ = _collecting_db()
        named, _residual = _split(
            shap_mod.build_explanation(db, forecast_id=uuid.uuid4(), feature_vector=fv, point=point)
        )
        # Same order as the booster's columns, same signs, same ranking.
        by_name = {r.feature_name: float(r.shap_impact) for r in named}
        assert set(by_name) == set(COLUMNS)
        for name, contribution in zip(COLUMNS, contribs, strict=True):
            if abs(float(contribution)) > 1e-6:
                assert (by_name[name] > 0) == (float(contribution) > 0), name
        ranked_model = [
            c
            for c, _v in sorted(
                zip(COLUMNS, contribs, strict=True), key=lambda t: -abs(float(t[1]))
            )
        ]
        ranked_rows = sorted(COLUMNS, key=lambda c: -abs(by_name[c]))
        assert ranked_rows[0] == ranked_model[0]


def test_booster_path_needs_the_vectors_trained_columns(monkeypatch) -> None:
    """A vector the booster cannot score falls back rather than mis-attributing."""
    bundle, frame = _trained_bundle()
    monkeypatch.setattr(shap_mod, "get_bundle", lambda: bundle)
    partial = {"lag_1": 40.0, "is_weekend": 1.0}
    fv = _row_vector(partial)
    db, _ = _collecting_db()
    named, residual = _split(
        shap_mod.build_explanation(db, forecast_id=uuid.uuid4(), feature_vector=fv, point=50.0)
    )
    assert {r.feature_name for r in named} == set(partial)  # the stub's drivers
    assert residual is not None
    assert residual.feature_value is None


def test_a_booster_that_declines_pred_contrib_falls_back(monkeypatch) -> None:
    """Serving must degrade to the stub, never raise (LightGBM can refuse)."""

    def _refuse(*a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("pred_contrib unsupported for this objective")

    bundle = {
        "model": {
            "feature_columns": ["lag_1"],
            "booster": MagicMock(predict=_refuse),
            "log_target": True,
        }
    }
    monkeypatch.setattr(shap_mod, "get_bundle", lambda: bundle)
    fv = _row_vector({"lag_1": 40.0})
    db, _ = _collecting_db()
    named, residual = _split(
        shap_mod.build_explanation(db, forecast_id=uuid.uuid4(), feature_vector=fv, point=50.0)
    )
    assert [r.feature_name for r in named] == ["lag_1"]
    assert float(named[0].shap_impact) == pytest.approx(
        shap_mod._stub_impact_for_feature("lag_1", 40.0, 50.0), abs=1e-3
    )
    assert residual is not None


# ------------------------------------------------------- the labelled stub


def test_without_a_bundle_the_rows_are_the_labelled_stub(monkeypatch) -> None:
    """No model, no attribution — but the placeholder still adds up to the point."""
    monkeypatch.setattr(shap_mod, "get_bundle", lambda: None)
    fv = _row_vector({"lag_1": 40.0, "roll_mean_7": 38.0, "is_weekend": 1.0})
    db, _ = _collecting_db()
    out = shap_mod.build_explanation(db, forecast_id=uuid.uuid4(), feature_vector=fv, point=50.0)
    named, residual = _split(out)

    assert [r.feature_name for r in named] == ["roll_mean_7", "lag_1", "is_weekend"]
    assert sum(float(r.shap_impact) for r in out) == pytest.approx(50.0, abs=_total_eps(named))
    assert residual is not None
    # Marked as the placeholder, so a reader is never left thinking a model
    # produced these numbers.
    assert {r.attribution_source for r in out} == {ATTRIBUTION_STUB}
    assert shap_mod.attribution_source_of(out) == ATTRIBUTION_STUB
    # And the residual of a no-model row is not exempt from the marking either.
    assert residual.attribution_source == ATTRIBUTION_STUB

    # Deterministic: the same input answers the same way.
    db2, _ = _collecting_db()
    again = shap_mod.build_explanation(db2, forecast_id=uuid.uuid4(), feature_vector=fv, point=50.0)
    assert [float(r.shap_impact) for r in again] == [float(r.shap_impact) for r in out]


def test_the_stub_never_invents_a_driver_the_row_did_not_have(monkeypatch) -> None:
    monkeypatch.setattr(shap_mod, "get_bundle", lambda: None)
    fv = _row_vector({"lag_1": 40.0})
    db, _ = _collecting_db()
    named, _residual = _split(
        shap_mod.build_explanation(db, forecast_id=uuid.uuid4(), feature_vector=fv, point=12.0)
    )
    assert [r.feature_name for r in named] == ["lag_1"]


def test_a_missing_feature_contributes_nothing(monkeypatch) -> None:
    monkeypatch.setattr(shap_mod, "get_bundle", lambda: None)
    assert shap_mod._stub_impact_for_feature("lag_1", None, 50.0) == 0.0


def test_attribution_source_of_surfaces_a_mixed_set(monkeypatch) -> None:
    """Rows from both paths are reported as mixed, not flattened to one."""
    rows = [
        ForecastExplanation(
            forecast_id=uuid.uuid4(),
            feature_name="lag_1",
            shap_impact=1.0,
            attribution_source=ATTRIBUTION_BOOSTER,
        ),
        ForecastExplanation(
            forecast_id=uuid.uuid4(),
            feature_name="roll_mean_7",
            shap_impact=2.0,
            attribution_source=ATTRIBUTION_STUB,
        ),
    ]
    assert shap_mod.attribution_source_of(rows) == "mixed"
    assert shap_mod.attribution_source_of([]) is None
    assert shap_mod.attribution_source_of(rows[:1]) == ATTRIBUTION_BOOSTER


# --------------------------------------------------------- schema plumbing


def test_the_column_exists_with_its_allowed_values() -> None:
    """The ORM carries the column and the constraint that keeps it meaningful."""
    column = ForecastExplanation.__table__.columns["attribution_source"]
    assert column.nullable is False
    # The name the metadata convention produces, which migration 020 and
    # database/schema.sql also use — this name is what a later autogenerate
    # compares against, so the three have to agree.
    assert "ck_forecast_explanations_attribution_source" in {
        c.name for c in ForecastExplanation.__table__.constraints
    }


def test_the_migration_chain_has_one_head_and_covers_this_column() -> None:
    """A migration adds the column, and the revision graph stays single-headed.

    Two heads (or a duplicate revision id) make ``alembic upgrade head``
    ambiguous, and a model column with no migration is a column that exists only
    in Python — the failure migration 017 was written to fix.
    """
    import ast
    from pathlib import Path

    versions = Path(__file__).resolve().parents[1] / "migrations" / "versions"

    def _declared(text: str) -> dict[str, object]:
        """``revision`` / ``down_revision`` as written.

        Parsed as Python rather than with a pattern: the declarations come in
        three shapes — bare, annotated (``down_revision: str | None = …``) and a
        multi-line tuple, which is what a merge migration (``010_merge_heads``)
        uses to join two branches. A line regex reads that tuple as empty and
        then reports the branches it joins as heads.
        """
        found: dict[str, object] = {}
        for node in ast.parse(text).body:
            if isinstance(node, ast.Assign):
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names, value = [node.target.id], node.value
            else:
                continue
            for name in names:
                if name in ("revision", "down_revision") and value is not None:
                    found[name] = ast.literal_eval(value)
        return found

    parents: dict[str, tuple[str, ...]] = {}
    adds_column = False
    for path in sorted(versions.glob("*.py")):
        text = path.read_text()
        try:
            declared = _declared(text)
        except ValueError as exc:  # pragma: no cover - a computed declaration
            raise AssertionError(f"{path.name}: declarations are not literals: {exc}") from exc
        name = declared.get("revision")
        assert isinstance(name, str), f"{path.name} declares no revision"
        assert name not in parents, f"duplicate revision {name}"
        # Alembic's own ``alembic_version.version_num`` is ``varchar(32)``: a
        # longer id fails on the UPDATE that records the upgrade, after the DDL
        # has been attempted. Caught on a scratch database, so it is pinned
        # here rather than left to the next author to discover in production.
        assert len(name) <= 32, f"{path.name}: revision id is {len(name)} chars, max 32"
        down = declared.get("down_revision")
        parents[name] = (down,) if isinstance(down, str) else tuple(down or ())
        if path.name.startswith("020") and "attribution_source" in text:
            adds_column = True

    assert adds_column, "no migration adds forecast_explanations.attribution_source"
    heads = set(parents) - {p for already in parents.values() for p in already}
    assert heads == {"020_explanation_attribution"}, f"revision heads: {sorted(heads)}"

    # Every parent must exist, and everything must hang off that head: a
    # revision outside the applied history is never run by ``alembic upgrade
    # head``, which is how a column ends up existing in Python only (the failure
    # migration 017 was written to fix). The history is a DAG — the merge in 010
    # makes two branches share ancestors — so the walk skips what it has seen
    # rather than treating a re-visit as a cycle.
    assert all(p in parents for chain in parents.values() for p in chain), "dangling down_revision"
    seen: set[str] = set()
    stack = ["020_explanation_attribution"]
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(parents.get(node, ()))
    assert seen == set(parents), f"unreachable: {sorted(set(parents) - seen)}"


# ------------------------------------------------------------ persistence


def test_prior_rows_for_the_forecast_are_replaced(monkeypatch) -> None:
    """A re-run (batch, backfill) must not stack a second explanation."""
    monkeypatch.setattr(shap_mod, "get_bundle", lambda: None)
    prior = [MagicMock(feature_name="lag_1"), MagicMock(feature_name=shap_mod.RESIDUAL_FEATURE)]
    db, added = _collecting_db(prior)
    fv = _row_vector({"lag_1": 40.0})
    out = shap_mod.build_explanation(db, forecast_id=uuid.uuid4(), feature_vector=fv, point=20.0)

    assert db.delete.call_count == 2
    assert added == list(out)
    assert [r.feature_name for r in out] == ["lag_1", shap_mod.RESIDUAL_FEATURE]
