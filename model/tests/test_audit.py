"""Audit tests against synthetic Recruit-shaped data."""

from __future__ import annotations

import pandas as pd


def _make_data(visits, stores, calendar, reservations):
    from diq_model.data.load import RecruitData

    return RecruitData(
        visits=visits, stores=stores, calendar=calendar, reservations=reservations
    )


def test_clean_data_passes_every_check(visits_df, stores_df, calendar_df, reservations_df):
    from diq_model.data.audit import audit_recruit

    clean = (
        visits_df[visits_df["air_store_id"] != "air_unknown"]
        .query("visitors >= 0")
        .drop_duplicates(subset=["air_store_id", "visit_date"], keep="first")
    )
    report = audit_recruit(_make_data(clean, stores_df, calendar_df, reservations_df))

    # Scale and span checks gate against the real Recruit figures; synthetic
    # fixtures are intentionally smaller, so only those two are waived here.
    failing = [name for name in report.failures if name not in {"audited_scale", "temporal_span"}]
    assert failing == [], f"unexpected audit failures: {report.summary()}"
    assert report.checks["duplicate_store_days"][0] is True
    assert report.checks["visitor_value_range"][0] is True


def test_scale_check_flags_synthetic_data(visits_df, stores_df, calendar_df, reservations_df):
    """The audited-scale gate must fail loudly when data is NOT full Recruit."""
    from diq_model.data.audit import audit_recruit

    data = _make_data(visits_df, stores_df, calendar_df, reservations_df)
    report = audit_recruit(data)
    assert report.checks["audited_scale"][0] is False
    assert "audited_scale" in report.failures


def test_duplicate_store_day_fails(visits_df, stores_df, calendar_df, reservations_df):
    from diq_model.data.audit import audit_recruit

    data = _make_data(visits_df, stores_df, calendar_df, reservations_df)
    report = audit_recruit(data)
    assert report.checks["duplicate_store_days"][0] is False
    assert "duplicate_store_days" in report.failures


def test_negative_visitors_fail(visits_df, stores_df, calendar_df, reservations_df):
    from diq_model.data.audit import audit_recruit

    data = _make_data(visits_df, stores_df, calendar_df, reservations_df)
    report = audit_recruit(data)
    assert report.checks["visitor_value_range"][0] is False


def test_unknown_store_id_fails_identifier_check(visits_df, stores_df, calendar_df, reservations_df):
    """The orphan store id breaks consistency only when store info is otherwise complete."""
    from diq_model.data.audit import audit_recruit

    # Drop air_unknown from store info so it becomes a genuine visit-only id.
    stores = stores_df[stores_df["air_store_id"] != "air_unknown"]
    data = _make_data(visits_df, stores, calendar_df, reservations_df)
    report = audit_recruit(data)
    assert report.checks["identifier_consistency"][0] is False


def test_coverage_is_computed_per_store(visits_df):
    from diq_model.data.audit import coverage_per_store

    clean = visits_df[visits_df["air_store_id"] != "air_unknown"]
    coverage = coverage_per_store(clean)
    assert len(coverage) == clean["air_store_id"].nunique()
    assert coverage.between(0.0, 1.0).all()


def test_duplicate_rows_do_not_inflate_coverage(visits_df):
    from diq_model.data.audit import coverage_per_store

    store = "air_000"
    before = coverage_per_store(visits_df[visits_df["air_store_id"] == store])[store]
    dup = visits_df[visits_df["air_store_id"] == store].iloc[[0]]
    after = coverage_per_store(pd.concat([visits_df, dup], ignore_index=True))[store]
    assert before == after  # a duplicate row must not change coverage


def test_summary_renders_pass_and_fail(visits_df, stores_df, calendar_df, reservations_df):
    from diq_model.data.audit import audit_recruit

    data = _make_data(visits_df, stores_df, calendar_df, reservations_df)
    report = audit_recruit(data)
    text = report.summary()
    assert "[PASS]" in text and "[FAIL]" in text
