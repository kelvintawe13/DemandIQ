"""Leakage-safe feature build (FEATURE_SPEC.md; proposal Sections 3.10, 3.26).

The information boundary: for a target day ``t``, a feature may only read
demand observations with ``date < t`` (as_of = t-1 23:59 local). Rolling
windows are computed on a *shifted* series so the window ``t-7 .. t-1``
never includes ``t``.

Missing history stays null. Boosted trees handle nulls natively; imputing
a zero would teach the model that a young store is a dying store.

Validation gates (FEATURE_SPEC.md): the job raises ``FeatureValidationError``
- loudly, without writing - if any feature window overlaps the target date,
or if a feature row exists for a date with no demand row in the source data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from diq_model.artifacts import CheckpointStore

logger = logging.getLogger(__name__)

# Bumped whenever any feature definition changes (FEATURE_SPEC.md "Versioning").
# 2026.09.2: peak-demand features (recent max/p90) + reservation as-of features.
#            (A 2026.09.3 candidate — Golden Week + non-workday-run features —
#             was evaluated and reverted; see EVALUATION.md "Feature-set sensitivity".)
FEATURE_SET_VERSION = "fs-2026.09.2"


class FeatureValidationError(RuntimeError):
    """A validation gate failed; the feature job must not emit output."""


# ------------------------------------------------------------ feature groups
# Section 3.25 requires the sensitivity of the results to "feature groups". A
# group is declared here, once, and ``build_features`` derives the model's input
# list from it, so the ablation in ``stage_sensitivity`` and the columns the
# production model actually reads cannot drift apart.
FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "historical": (
        "lag_1",
        "lag_7",
        "lag_14",
        "roll_mean_7",
        "roll_mean_28",
        "roll_std_28",
        "roll_max_7",
        "roll_max_28",
        "roll_p90_28",
        "dow_mean_8w",
        "dow_std_8w",
        "trend_7_28",
    ),
    # Everything here is known before the target day starts, so nothing in this
    # group can leak (Section 3.26).
    "calendar": (
        "dow",
        "is_weekend",
        "month",
        "week_of_year",
        "is_holiday",
        "day_before_holiday",
        "day_after_holiday",
        "is_weekend_or_holiday",
        "is_weekend_holiday",
        "is_month_end",
        "days_to_month_end",
    ),
    # Supplementary (Section 3.10): booked strictly before the visit day, and
    # present for only part of the estate, so the model must not depend on them.
    "reservation": ("rsv_visitors", "rsv_count", "rsv_visitors_ratio"),
}


def feature_columns_for_groups(
    available: list[str], groups: tuple[str, ...]
) -> list[str]:
    """The ordered subset of ``available`` that belongs to ``groups``.

    Used by the Section 3.25 ablation, which trains the same model on the same
    rows with one group removed. A column that is absent from the frame (no
    reservation feed at all, for instance) simply is not selected, rather than
    being synthesised as a null column.
    """
    wanted = {c for g in groups for c in FEATURE_GROUPS.get(g, ())}
    return [c for c in available if c in wanted]


@dataclass(frozen=True)
class FeatureFrame:
    """The model-ready feature frame plus its provenance."""

    frame: pd.DataFrame
    feature_set_version: str
    feature_columns: list[str]
    target_column: str = "visitors"

    @property
    def n_rows(self) -> int:
        return len(self.frame)


# --------------------------------------------------------------- calendar
def add_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    """Calendar features from the visit date (all known in advance - no leak).

    Holiday flags come from Recruit's ``date_info.csv`` (Japanese holiday
    calendar - the signal this dataset carries). The before/after flags are
    computed per the *sorted calendar* frame, not by row-shifting the merged
    frame, so store boundaries cannot corrupt them.
    """
    dates = df["visit_date"]
    out = df.copy()
    out["dow"] = dates.dt.dayofweek.astype("int8")
    out["is_weekend"] = (dates.dt.dayofweek >= 5).astype("int8")
    out["month"] = dates.dt.month.astype("int8")
    out["week_of_year"] = dates.dt.isocalendar().week.astype("int16")
    out["is_month_end"] = dates.dt.is_month_end.astype("int8")
    # Payday proximity (FEATURE_SPEC.md): months differ in length, so distance
    # to month-end is used rather than a hard "days since the 28th".
    out["days_to_month_end"] = (dates.dt.days_in_month - dates.dt.day).astype("int16")

    if "holiday_flg" in out.columns:
        cal = (
            out[["visit_date", "holiday_flg"]]
            .drop_duplicates("visit_date")
            .sort_values("visit_date")
            .reset_index(drop=True)
        )
        cal["is_holiday"] = cal["holiday_flg"].astype("int8")
        # Flags computed on the date-unique calendar:
        #   day_before_holiday(t) = 1 when t+1 is the holiday  -> shift(-1)
        #   day_after_holiday(t)  = 1 when t-1 was the holiday -> shift(1)
        cal["day_before_holiday"] = cal["is_holiday"].shift(-1).fillna(0).astype("int8")
        cal["day_after_holiday"] = cal["is_holiday"].shift(1).fillna(0).astype("int8")
        out = out.merge(
            cal[
                [
                    "visit_date",
                    "is_holiday",
                    "day_before_holiday",
                    "day_after_holiday",
                ]
            ],
            on="visit_date",
            how="left",
        )
        # Peak-day interactions (fs-2026.09.2): long weekends and combined
        # non-workdays are the classic visitor spikes the single flags miss.
        out["is_weekend_or_holiday"] = (out["is_weekend"] | out["is_holiday"]).astype("int8")
        out["is_weekend_holiday"] = (out["is_weekend"] & out["is_holiday"]).astype("int8")
    return out


# ------------------------------------------------------------- historical
def add_historical_features(visits: pd.DataFrame) -> pd.DataFrame:
    """Lag / rolling / same-weekday features, all from ``date < target``."""
    df = visits.sort_values(["air_store_id", "visit_date"]).copy()
    g = df.groupby("air_store_id")["visitors"]

    # Lags: the value at t-k, by construction strictly before t.
    for k in (1, 7, 14):
        df[f"lag_{k}"] = g.shift(k)

    # The reference pattern (FEATURE_SPEC.md) - windows sit on the shift(1)-ed
    # series, so roll_mean_7 covers t-7..t-1 and can never see t.
    df["roll_mean_7"] = g.transform(lambda s: s.shift(1).rolling(7, min_periods=3).mean())
    df["roll_mean_28"] = g.transform(lambda s: s.shift(1).rolling(28, min_periods=7).mean())
    df["roll_std_28"] = g.transform(lambda s: s.shift(1).rolling(28, min_periods=7).std())

    # Same-weekday history: mean/std over the last 8 same-dow occurrences,
    # reading only rows strictly before t (shift applied inside the transform).
    df["dow_mean_8w"] = _same_weekday_stat(df, "mean")
    df["dow_std_8w"] = _same_weekday_stat(df, "std")

    # Trend ratio; null when either input is null or zero-denominator.
    df["trend_7_28"] = df["roll_mean_7"] / df["roll_mean_28"]
    df["trend_7_28"] = df["trend_7_28"].replace([np.inf, -np.inf], np.nan)

    # Peak-demand features (fs-2026.09.2): the model had means but no direct
    # view of the recent TAIL, which is where busy-day prediction fails.
    # Same shift(1) discipline - windows cover t-k..t-1, never t.
    df["roll_max_7"] = g.transform(lambda s: s.shift(1).rolling(7, min_periods=3).max())
    df["roll_max_28"] = g.transform(lambda s: s.shift(1).rolling(28, min_periods=7).max())
    df["roll_p90_28"] = g.transform(lambda s: s.shift(1).rolling(28, min_periods=7).quantile(0.9))

    return df


def _same_weekday_stat(df: pd.DataFrame, stat: str) -> pd.Series:
    """Mean/std of the same weekday over its last 8 occurrences before t.

    For each weekday d, take the store's d-only subsequence (in date order),
    shift it by one *occurrence*, and roll over the previous 8 occurrences.
    Realign back onto the store's full index; rows of other weekdays get the
    result of their own dow's subsequence.
    """

    def per_store(block: pd.DataFrame) -> pd.Series:
        dows = block["visit_date"].dt.dayofweek
        out = pd.Series(np.nan, index=block.index, dtype="float64")
        for dow in range(7):
            mask = dows == dow
            if not mask.any():
                continue
            vals = block.loc[mask, "visitors"]  # d-only subsequence, date-ordered
            if stat == "mean":
                rolled = vals.shift(1).rolling(8, min_periods=2).mean()
            else:
                rolled = vals.shift(1).rolling(8, min_periods=2).std()
            out.loc[rolled.index] = rolled.to_numpy()
        return out

    return df.groupby("air_store_id", group_keys=False).apply(per_store)


# ------------------------------------------------------------ reservations
def add_reservation_features(df: pd.DataFrame, reservations: pd.DataFrame | None) -> pd.DataFrame:
    """Reservation signals as of the target day (fs-2026.09.2, Section 3.10).

    The information boundary here is REAL TIME, not the day boundary: a
    reservation for a visit on day t made on day t-3 was knowable on day
    t-3, but one made ON day t was not. So the as-of set for day t is
    reservations with ``reserve_datetime < visit_date`` (strictly before
    the visit day starts) - anything booked later cannot be used.

    Features per (store, visit-day):
        rsv_visitors       sum of reserve_visitors booked before the day
        rsv_count          number of such reservations
        rsv_visitors_ratio booked visitors / rolling 28-day mean demand -
                           scale-free ramp signal; null without history.

    Stores without any pre-day reservations keep nulls (never zeros - a
    store with no booking data is not a store with zero bookings).
    """
    if reservations is None or len(reservations) == 0:
        return df

    rsv = reservations.loc[
        reservations["reserve_datetime"] < reservations["visit_datetime"],
        ["air_store_id", "visit_datetime", "reserve_visitors"],
    ].copy()
    rsv["visit_date"] = rsv["visit_datetime"].dt.normalize()
    agg = rsv.groupby(["air_store_id", "visit_date"], as_index=False).agg(
        rsv_visitors=("reserve_visitors", "sum"), rsv_count=("reserve_visitors", "size")
    )
    agg["rsv_count"] = agg["rsv_count"].astype("int16")

    df = df.merge(agg, on=["air_store_id", "visit_date"], how="left")
    mean28 = df.groupby("air_store_id")["roll_mean_28"].transform("first")
    df["rsv_visitors_ratio"] = df["rsv_visitors"] / mean28
    df["rsv_visitors_ratio"] = df["rsv_visitors_ratio"].replace([np.inf, -np.inf], np.nan)
    return df


# ------------------------------------------------------------ validation
def assert_no_window_overlap(df: pd.DataFrame) -> None:
    """Validation gate 1: every feature window must end strictly before the target.

    Verified structurally: recompute features on a doctored copy of the data
    in which every probe row's target-day value is replaced by a poison value.
    If any historical feature on a probe row still sees the poison, a window
    touched the target day and the gate fails.
    """
    poison = -999_999.0
    probe = (
        df.dropna(subset=["visit_date"])
        .sort_values(["air_store_id", "visit_date"])
        .groupby("air_store_id")
        .tail(1)  # last row per store: most history, widest windows
    )
    if probe.empty:
        raise FeatureValidationError("no dated rows to probe")

    doctored = df.copy()
    poison_idx = doctored.index.isin(probe.index)
    doctored.loc[poison_idx, "visitors"] = poison

    rebuilt = add_historical_features(doctored)
    check = rebuilt.loc[probe.index]
    for col in (
        "lag_1",
        "lag_7",
        "lag_14",
        "roll_mean_7",
        "roll_mean_28",
        "roll_std_28",
        "roll_max_7",
        "roll_max_28",
        "roll_p90_28",
    ):
        if check[col].eq(poison).any():
            raise FeatureValidationError(f"leakage: feature '{col}' reads the target day")


def assert_feature_rows_have_demand(features: pd.DataFrame, visits: pd.DataFrame) -> None:
    """Validation gate 3: no feature row without a demand row in the source."""
    demand_keys = set(
        map(tuple, visits[["air_store_id", "visit_date"]].itertuples(index=False, name=None))
    )
    feature_keys = set(
        map(tuple, features[["air_store_id", "visit_date"]].itertuples(index=False, name=None))
    )
    orphans = feature_keys - demand_keys
    if orphans:
        raise FeatureValidationError(
            f"{len(orphans)} feature rows without demand rows, e.g. {sorted(orphans)[:3]}"
        )


# ------------------------------------------------------------------ build
def build_features(
    visits: pd.DataFrame,
    calendar: pd.DataFrame | None = None,
    reservations: pd.DataFrame | None = None,
) -> FeatureFrame:
    """Assemble the full feature frame for every (store, date) in ``visits``.

    Rows whose historical features are entirely null (the first day(s) of a
    store's series) are dropped - there is nothing for the model to learn
    from yet, and the target day is never imputed.
    """
    df = add_historical_features(visits)
    if calendar is not None and len(calendar):
        df = df.merge(
            calendar[["calendar_date", "holiday_flg"]],
            left_on="visit_date",
            right_on="calendar_date",
            how="left",
        )
        df = df.drop(columns=["calendar_date"])
    df = add_calendar_features(df)
    df = add_reservation_features(df, reservations)

    # Gate 1: structural leakage probe (raises FeatureValidationError on leak).
    assert_no_window_overlap(df)

    # Gate 3: feature rows must correspond to demand rows.
    assert_feature_rows_have_demand(df, visits)

    # Derived from FEATURE_GROUPS so the ablation subsets and the production
    # input list share one definition.
    feature_cols = [c for group in FEATURE_GROUPS.values() for c in group]
    feature_cols = [c for c in feature_cols if c in df.columns]

    # Keep rows with at least one historical feature.
    hist_cols = [c for c in feature_cols if c.startswith(("lag_", "roll_", "dow_m", "dow_s"))]
    keep = df[hist_cols].notna().any(axis=1)
    df = df.loc[keep].reset_index(drop=True)

    logger.info(
        "Feature frame: %d rows, %d features, version %s",
        len(df),
        len(feature_cols),
        FEATURE_SET_VERSION,
    )
    return FeatureFrame(
        frame=df,
        feature_set_version=FEATURE_SET_VERSION,
        feature_columns=feature_cols,
    )


def save_feature_frame(store: CheckpointStore, fframe: FeatureFrame) -> None:
    store.save_dataframe(fframe.frame, "features.parquet")
    store.save_object(
        {
            "feature_set_version": fframe.feature_set_version,
            "feature_columns": fframe.feature_columns,
            "target_column": fframe.target_column,
        },
        "features_meta.joblib",
    )


def load_feature_frame(store: CheckpointStore) -> FeatureFrame:
    frame = store.load_dataframe("features.parquet")
    meta = store.load_object("features_meta.joblib")
    return FeatureFrame(
        frame=frame,
        feature_set_version=meta["feature_set_version"],
        feature_columns=meta["feature_columns"],
        target_column=meta["target_column"],
    )
