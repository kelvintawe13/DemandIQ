"""Dataset audit (Sections 3.7.1, 3.8).

Implements every Recruit check the proposal commits to:

1. duplicate restaurant-date combinations        (3.7.1)
2. date-format consistency / parse failures      (3.7.1)
3. missing observations -> per-store gaps        (3.7.1)
4. restaurant-level coverage gaps                (3.7.1: 85% avg, 210 stores < 80%)
5. anomalous visitor counts                      (3.7.1)
6. inconsistent identifiers                      (3.7.1)
7. temporal ordering                             (3.7.1)

Plus reservation coverage (3.4.1: 314 of 821 stores) as context.

The audit is read-only and never mutates the data: findings are reported,
not silently fixed. Every result carries the audited proposal figure it
should be compared against.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from diq_model.constants import (
    EXPECTED_STORES,
    EXPECTED_VISIT_ROWS,
    RESERVE_RESTAURANTS,
    VISIT_DATE_MAX,
    VISIT_DATE_MIN,
)
from diq_model.data.load import RecruitData, load_recruit

logger = logging.getLogger(__name__)


@dataclass
class AuditReport:
    """Structured result of one audit run.

    ``checks`` maps check name -> (passed: bool, detail: str). ``passed`` is
    an aggregate: a check that merely *reports* (e.g. coverage stats) counts
    as passed if it produced numbers; a check that validates a hard rule
    (duplicates, negatives) fails if the rule is violated.
    """

    checks: dict[str, tuple[bool, str]] = field(default_factory=dict)

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.checks[name] = (passed, detail)

    @property
    def ok(self) -> bool:
        return all(passed for passed, _ in self.checks.values())

    @property
    def failures(self) -> list[str]:
        return [name for name, (passed, _) in self.checks.items() if not passed]

    def summary(self) -> str:
        lines = ["Recruit dataset audit", "-" * 60]
        for name, (passed, detail) in self.checks.items():
            marker = "PASS" if passed else "FAIL"
            lines.append(f"[{marker}] {name}: {detail}")
        lines.append("-" * 60)
        lines.append("OVERALL: PASS" if self.ok else f"OVERALL: FAIL ({', '.join(self.failures)})")
        return "\n".join(lines)


def _check_duplicates(visits: pd.DataFrame, report: AuditReport) -> None:
    dupes = int(visits.duplicated(subset=["air_store_id", "visit_date"]).sum())
    report.add(
        "duplicate_store_days",
        dupes == 0,
        f"{dupes} duplicate (store, date) pairs"
        + ("" if dupes == 0 else " - must be resolved before feature build"),
    )


def _check_missing_dates(visits: pd.DataFrame, report: AuditReport) -> None:
    nulls = int(visits["visit_date"].isna().sum())
    report.add("date_parse_failures", nulls == 0, f"{nulls} unparseable visit_date values")


def _check_anomalous_visitors(visits: pd.DataFrame, report: AuditReport) -> None:
    negative = int((visits["visitors"] < 0).sum())
    zero = int((visits["visitors"] == 0).sum())
    p999 = float(visits["visitors"].quantile(0.999))
    report.add(
        "visitor_value_range",
        negative == 0,
        f"min={int(visits['visitors'].min())}, max={int(visits['visitors'].max())}, "
        f"{negative} negative, {zero} zero, p99.9={p999:.0f}",
    )


def _check_identifiers(
    visits: pd.DataFrame, stores: pd.DataFrame, reservations: pd.DataFrame, report: AuditReport
) -> None:
    visit_ids = set(visits["air_store_id"].unique())
    store_ids = set(stores["air_store_id"].unique())
    reserve_ids = set(reservations["air_store_id"].unique()) if len(reservations) else set()

    orphan_visits = visit_ids - store_ids
    orphan_reserves = reserve_ids - store_ids
    detail = (
        f"{len(visit_ids)} stores in visits, {len(store_ids)} in store info, "
        f"{len(visit_ids - store_ids)} visit-only, {len(reserve_ids - store_ids)} reserve-only"
    )
    report.add("identifier_consistency", not orphan_visits, detail)


def _check_temporal_order(visits: pd.DataFrame, report: AuditReport) -> None:
    span = (visits["visit_date"].min(), visits["visit_date"].max())
    expected = (pd.Timestamp(VISIT_DATE_MIN), pd.Timestamp(VISIT_DATE_MAX))
    ok = span == expected
    report.add(
        "temporal_span",
        ok,
        f"{span[0].date()} .. {span[1].date()}"
        + ("" if ok else f" (expected {expected[0].date()} .. {expected[1].date()})"),
    )


def _check_scale(visits: pd.DataFrame, report: AuditReport) -> None:
    ok = len(visits) == EXPECTED_VISIT_ROWS and visits["air_store_id"].nunique() == EXPECTED_STORES
    report.add(
        "audited_scale",
        ok,
        f"{len(visits):,} rows / {visits['air_store_id'].nunique()} stores"
        + ("" if ok else f" (proposal: {EXPECTED_VISIT_ROWS:,} / {EXPECTED_STORES})"),
    )


def coverage_per_store(visits: pd.DataFrame) -> pd.DataFrame:
    """Per-store coverage: observed days / calendar days in the store's span.

    The proposal's 85%-average / 210-stores-below-80% figures were computed
    this way (store span, not the full 2016-01-01..2017-04-22 window).
    """
    full_index = pd.date_range(visits["visit_date"].min(), visits["visit_date"].max(), freq="D")
    span_per_store = visits.groupby("air_store_id")["visit_date"].agg(["min", "max"])
    # nunique, not count: a duplicated (store, date) row must not inflate coverage.
    observed = visits.groupby("air_store_id")["visit_date"].nunique()

    coverage = (observed / (span_per_store["max"] - span_per_store["min"]).dt.days.add(1)).rename(
        "coverage"
    )
    n_below_80 = int((coverage < 0.80).sum())
    logger.debug(
        "Coverage: mean=%.3f, stores<80%%=%d, full-window span=%d days",
        coverage.mean(),
        n_below_80,
        len(full_index),
    )
    return coverage.sort_values()


def _check_coverage(visits: pd.DataFrame, report: AuditReport) -> None:
    coverage = coverage_per_store(visits)
    report.add(
        "store_coverage",
        True,  # reports, does not gate: real data will differ from the proposal's figures
        f"mean={coverage.mean():.1%}, stores<80%={int((coverage < 0.80).sum())} "
        f"(proposal reference: 85% mean, 210 stores)",
    )


def _check_reservation_coverage(
    visits: pd.DataFrame, reservations: pd.DataFrame, report: AuditReport
) -> None:
    n_stores = visits["air_store_id"].nunique()
    n_reserve_stores = (
        reservations.loc[reservations["air_store_id"].isin(visits["air_store_id"].unique()),
                         "air_store_id"].nunique()
        if len(reservations)
        else 0
    )
    report.add(
        "reservation_coverage",
        True,  # context only: confirms the supplementary-feature decision in Section 3.10
        f"{n_reserve_stores} of {n_stores} stores have reservation logs "
        f"(proposal: {RESERVE_RESTAURANTS} of 821)",
    )


def audit_recruit(data: RecruitData) -> AuditReport:
    """Run every Section 3.7.1 check against loaded Recruit data."""
    report = AuditReport()
    visits = data.visits

    _check_scale(visits, report)
    _check_duplicates(visits, report)
    _check_missing_dates(visits, report)
    _check_anomalous_visitors(visits, report)
    _check_identifiers(visits, data.stores, data.reservations, report)
    _check_temporal_order(visits, report)
    _check_coverage(visits, report)
    _check_reservation_coverage(visits, data.reservations, report)
    return report


def audit_recruit_root(root: str | Path) -> AuditReport:
    """Convenience: load from a dataset folder then audit."""
    return audit_recruit(load_recruit(root))
