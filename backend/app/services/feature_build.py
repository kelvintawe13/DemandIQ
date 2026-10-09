"""Feature build — the leakage boundary (ARCHITECTURE.md §2).

Every feature for ``(location_id, target_date)`` is materialised with an
explicit ``as_of`` timestamp equal to the moment the forecast would be made.

Invariants enforced by this module:

* A feature may only read rows with ``date < target_date``. Rolling windows
  are computed on a shifted series. If any feature's source window would
  overlap ``target_date`` the build **refuses to run** (raises
  :class:`FeatureLeakageError`).
* Reservation-supplementary features are included only when the reservation
  was known *before* ``as_of``.
* ``as_of`` must be strictly before ``target_date`` at day granularity;
  otherwise the forecast would be peeking at the future.

This is the single most important correctness property in the system
(``IMPLEMENTATION_ROADMAP.md`` non-negotiable 1). The accompanying test
``test_feature_window_no_leak`` fails the build if the assertion is removed.
"""

from __future__ import annotations

import calendar
import datetime
import logging
import math
import statistics
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.location import DemandObservation, Location
from app.models.reservation import Reservation

logger = logging.getLogger(__name__)


class FeatureLeakageError(AssertionError):
    """Raised when a feature window would overlap the target date."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


# ---------------------------------------------------------------------------
# Window helpers
# ---------------------------------------------------------------------------


def _assert_no_leak(
    *,
    target_date: datetime.date,
    as_of: datetime.datetime,
    window_end_exclusive: datetime.date,
    window_start: datetime.date | None = None,
    feature: str = "unknown",
) -> None:
    """Assert that a feature's source window does not include the target.

    Args:
        target_date: Day being forecast.
        as_of: Forecast creation timestamp.
        window_end_exclusive: Day after the last day in the window (exclusive
            upper bound). Must be ``<= target_date``.
        window_start: Inclusive start of the window (for diagnostics).
        feature: Feature name for the error message.

    Raises:
        FeatureLeakageError: If the window overlaps ``target_date`` or if
            ``as_of`` is not before ``target_date``.
    """
    # as_of must be before target_date (at day granularity).
    if as_of.date() >= target_date:
        raise FeatureLeakageError(
            f"feature '{feature}' leakage: as_of {as_of.date().isoformat()} "
            f"is not before target_date {target_date.isoformat()}",
            field=feature,
        )
    if window_end_exclusive > target_date:
        raise FeatureLeakageError(
            f"feature '{feature}' leakage: window [{window_start}, "
            f"{window_end_exclusive}) overlaps target_date {target_date.isoformat()}",
            field=feature,
        )


def _history_for_location(
    db: Session,
    location_id: uuid.UUID,
    *,
    target_date: datetime.date,
    as_of: datetime.datetime,
) -> dict[datetime.date, int]:
    """Load demand history strictly before ``target_date``.

    The ``as_of`` timestamp is also respected: rows whose ``recorded_at`` is
    after ``as_of`` are excluded when that column is populated (covers late
    corrections that would not have been known at forecast time).

    However, the primary leakage guard is the ``date < target_date`` filter;
    ``as_of`` is an additional guard for reservation-supplementary data.
    """
    # Core leakage assertion for the history fetch itself.
    # Any window that would read ``date >= target_date`` is rejected upfront.
    _assert_no_leak(
        target_date=target_date,
        as_of=as_of,
        window_end_exclusive=target_date,
        window_start=None,
        feature="history_fetch",
    )

    rows = db.scalars(
        select(DemandObservation)
        .where(
            DemandObservation.location_id == location_id,
            DemandObservation.date < target_date,  # <-- leakage boundary
        )
        .order_by(DemandObservation.date.asc())
    ).all()

    # Respect as_of for late-arriving corrections (best-effort; if
    # recorded_at is None, keep the row).
    filtered: dict[datetime.date, int] = {}
    for r in rows:
        if r.recorded_at is not None and r.recorded_at > as_of:
            continue
        filtered[r.date] = r.demand_qty
    return filtered


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FeatureVector:
    """Materialised features for one ``(location_id, target_date)``."""

    location_id: uuid.UUID
    target_date: datetime.date
    as_of: datetime.datetime
    features: dict[str, Any]
    history_days: int


def build_features(
    db: Session,
    *,
    location_id: uuid.UUID,
    target_date: datetime.date,
    as_of: datetime.datetime,
    reservation_known_before_as_of: dict[datetime.date, dict[str, Any]] | None = None,
    holidays: frozenset[datetime.date] | None = None,
) -> FeatureVector:
    """Materialise the full feature set for a forecast point.

    Feature groups:
    * **Historical:** lag-1, lag-7, rolling 7/28-day mean, same-weekday mean,
      28-day volatility (all computed on ``date < target_date``).
    * **Calendar:** day-of-week, weekend flag, month, public holiday, day
      before/after holiday.
    * **Location:** service type / district / cuisine / capacity when available
      on the ``locations`` row.
    * **Reservation (supplementary):** counts and lead times for
      ``target_date`` only if supplied and only when known before ``as_of``.

    Args:
        db: Database session.
        location_id: Location to featurise.
        target_date: Day being forecast.
        as_of: Forecast creation timestamp (must be ``< target_date``).
        reservation_known_before_as_of: Optional mapping
            ``{date: {reserved_count, lead_time_days, known_at}}``. Only
            entries where ``known_at <= as_of`` are used.
        holidays: Exact holiday dates to flag. When serving a trained
            bundle, pass the calendar the bundle carries (the training
            features assumed it); ``None`` falls back to the locale stub.

    Returns:
        :class:`FeatureVector` with the computed feature dict.

    Raises:
        FeatureLeakageError: If any feature window would include
            ``target_date`` (fail-closed rather than emit a bad window).
    """
    # --- Top-level leakage guard -------------------------------------------
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=datetime.UTC)
    _assert_no_leak(
        target_date=target_date,
        as_of=as_of,
        window_end_exclusive=target_date,
        feature="as_of_boundary",
    )

    loc: Location | None = db.get(Location, location_id)
    history = _history_for_location(db, location_id, target_date=target_date, as_of=as_of)

    # Build sorted date list for window slicing (all < target_date by construction).
    sorted_dates = sorted(history.keys())

    def _window(days: int, feature_name: str) -> tuple[list[int], datetime.date, datetime.date]:
        """Return values, start and end for a ``days``-day window ending at target_date."""
        end_exclusive = target_date
        start = target_date - datetime.timedelta(days=days)
        _assert_no_leak(
            target_date=target_date,
            as_of=as_of,
            window_end_exclusive=end_exclusive,
            window_start=start,
            feature=feature_name,
        )
        vals = [history[d] for d in sorted_dates if start <= d < end_exclusive]
        return vals, start, end_exclusive

    features: dict[str, Any] = {}

    # --- Historical --------------------------------------------------------
    # Lag features — point lookups, still guarded.
    for lag, name in [(1, "lag_1"), (7, "lag_7")]:
        d = target_date - datetime.timedelta(days=lag)
        _assert_no_leak(
            target_date=target_date,
            as_of=as_of,
            window_end_exclusive=d + datetime.timedelta(days=1),
            window_start=d,
            feature=name,
        )
        features[name] = history.get(d)

    for n, name in [(7, "roll_mean_7"), (28, "roll_mean_28")]:
        vals, _, _ = _window(n, name)
        features[name] = round(statistics.mean(vals), 2) if vals else None
        features[f"{name}_count"] = len(vals)

    # Same-weekday mean (same dow in the last 4 weeks, strictly before target).
    weekday = target_date.weekday()
    sw_vals: list[int] = []
    for w in range(1, 5):
        d = target_date - datetime.timedelta(days=7 * w)
        _assert_no_leak(
            target_date=target_date,
            as_of=as_of,
            window_end_exclusive=d + datetime.timedelta(days=1),
            window_start=d,
            feature="same_weekday_mean",
        )
        if d in history:
            sw_vals.append(history[d])
    features["same_weekday_mean"] = round(statistics.mean(sw_vals), 2) if sw_vals else None

    # 28-day volatility (sample stdev).
    vol_vals, _, _ = _window(28, "volatility_28")
    if len(vol_vals) >= 2:
        features["volatility_28"] = round(statistics.stdev(vol_vals), 2)
    else:
        features["volatility_28"] = None

    # --- Calendar ----------------------------------------------------------
    features["dow"] = weekday
    features["is_weekend"] = weekday >= 5
    features["month"] = target_date.month
    features["is_public_holiday"] = _is_public_holiday(target_date, holidays)
    features["day_before_holiday"] = _is_public_holiday(
        target_date + datetime.timedelta(days=1), holidays
    )
    features["day_after_holiday"] = _is_public_holiday(
        target_date - datetime.timedelta(days=1), holidays
    )

    # --- Location ----------------------------------------------------------
    if loc is not None:
        features["location_area"] = loc.area
        features["location_cuisine"] = loc.cuisine
        # opened_on -> days since opening (capped, not leakage-sensitive)
        if loc.opened_on is not None:
            delta = target_date - loc.opened_on
            features["days_since_opening"] = max(0, delta.days)
        else:
            features["days_since_opening"] = None

    # --- Reservation (supplementary, as_of-gated) --------------------------
    reservation_count: int | None = None
    reservation_lead: float | None = None
    if reservation_known_before_as_of is not None:
        entry = reservation_known_before_as_of.get(target_date)
        if entry is not None:
            known_at = entry.get("known_at")
            # Only use if known before as_of.
            use = True
            if isinstance(known_at, datetime.datetime):
                if known_at.tzinfo is None:
                    known_at = known_at.replace(tzinfo=datetime.UTC)
                use = known_at <= as_of
            elif known_at is not None:
                # Unknown timestamp type — be conservative and skip.
                use = False
            if use:
                reservation_count = entry.get("reserved_count")
                reservation_lead = entry.get("lead_time_days")
            else:
                logger.info(
                    "reservation for %s excluded: known_at %s > as_of %s",
                    target_date,
                    known_at,
                    as_of,
                )
    features["reservation_count"] = reservation_count
    features["reservation_lead_time_days"] = reservation_lead

    # --- Final leakage audit (defence in depth) ----------------------------
    # Ensure no feature value was derived from target_date itself.
    # The history dict is already filtered, so this is a sanity check that
    # future changes to the windowing logic cannot silently reintroduce
    # leakage. Raised (not asserted) on purpose: `assert` statements are
    # stripped under `python -O`, which would delete this guard exactly when it
    # is the last line of defence.
    if target_date in history:
        raise FeatureLeakageError("history must not contain target_date")

    return FeatureVector(
        location_id=location_id,
        target_date=target_date,
        as_of=as_of,
        features=features,
        history_days=len(history),
    )


def _reservations_for_location(
    db: Session, location_id: uuid.UUID, *, target_date: datetime.date
) -> list[Any]:
    """Pre-day booking aggregates for ``target_date`` (training contract).

    Rows are per (location, visit-date) aggregates of reservations *booked
    strictly before the visit day starts* — the day-start boundary is applied
    at ingestion, mirroring the training pipeline's
    ``reserve_datetime < visit_datetime`` filter (Section 3.10).
    """
    return list(
        db.scalars(
            select(Reservation).where(
                Reservation.location_id == location_id,
                Reservation.date == target_date,
            )
        ).all()
    )


def _is_public_holiday(d: datetime.date, holidays: frozenset[datetime.date] | None = None) -> bool:
    """Holiday lookup — exact calendar when provided, locale stub otherwise.

    Args:
        d: Date to test.
        holidays: Exact holiday dates (e.g. the calendar carried by the
            serving bundle, which is the calendar the training features
            assumed). ``None`` falls back to the built-in locale stub below.
    """
    if holidays is not None:
        return d in holidays
    # Example fixed holidays for Rwanda (extend as needed).
    fixed = {(1, 1), (7, 4), (12, 25)}
    return (d.month, d.day) in fixed


# Backwards-compatible alias used by older docs.
def materialise_features(*args: Any, **kwargs: Any) -> FeatureVector:
    """Alias for :func:`build_features`."""
    return build_features(*args, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Full model feature set — aligned with diq_model fs-2026.09.2
# ---------------------------------------------------------------------------


def _percentile_linear(values: list[float], q: float) -> float:
    """Linear-interpolation percentile (numpy 'linear' method), pure python."""
    s = sorted(values)
    n = len(s)
    if n == 1:
        return float(s[0])
    pos = (n - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(s[int(pos)])
    frac = pos - lo
    return float(s[lo] * (1 - frac) + s[hi] * frac)


def build_model_features(
    db: Session,
    *,
    location_id: uuid.UUID,
    target_date: datetime.date,
    as_of: datetime.datetime,
    holidays: frozenset[datetime.date] | None = None,
) -> FeatureVector:
    """Materialise the FULL training feature set for the LightGBM bundle.

    Produces the exact fs-2026.09.2 feature vector the exported LightGBM
    bundle was trained on (26 columns: positional lags and rolling stats,
    8-week same-weekday stats, trend, calendar + holiday-interaction flags,
    reservation features). Reservation features are emitted as nulls
    (LightGBM's native-missing handling) until reservation data is ingested
    per location.

    Leakage discipline is inherited: every window reads only
    ``date < target_date`` rows (guarded by :func:`_history_for_location`).
    """
    fv = build_features(
        db, location_id=location_id, target_date=target_date, as_of=as_of, holidays=holidays
    )
    features: dict[str, Any] = dict(fv.features)
    history = _history_for_location(db, location_id, target_date=target_date, as_of=as_of)
    sorted_dates = sorted(history.keys())
    vals = [float(history[d]) for d in sorted_dates]
    n = len(vals)

    def _tail(k: int) -> list[float]:
        """Last k observed values (positional — mirrors training windows)."""
        return vals[-k:]

    # --- Historical ---------------------------------------------------------
    # IMPORTANT: this must reproduce diq_model's ``add_historical_features``
    # *exactly*. That implementation lags/rolls POSITIONALLY over the store's
    # observed rows (``groupby.shift`` / ``rolling`` on the sorted series),
    # not over calendar days — stores have missing days, so "7 rows back" is
    # not always "t-7 calendar". Serving must use the training definition;
    # anything else silently shifts the feature distribution the booster was
    # fit on. Window sizes, min_periods and ddof are mirrored 1:1, and no
    # rounding is applied (the training frame is unrounded).
    for k, name in ((1, "lag_1"), (7, "lag_7"), (14, "lag_14")):
        features[name] = vals[-k] if n >= k else None

    features["roll_mean_7"] = statistics.fmean(_tail(7)) if n >= 3 else None
    features["roll_mean_28"] = statistics.fmean(_tail(28)) if n >= 7 else None
    features["roll_std_28"] = statistics.stdev(_tail(28)) if n >= 7 else None
    features["roll_max_7"] = max(_tail(7)) if n >= 3 else None
    features["roll_max_28"] = max(_tail(28)) if n >= 7 else None
    features["roll_p90_28"] = _percentile_linear(_tail(28), 90) if n >= 7 else None

    # Same-weekday: mean/std over the last 8 same-dow *observed occurrences*
    # strictly before the target (min 2), skipping missing days — matching
    # the per-dow subsequence ``shift(1).rolling(8, min_periods=2)`` in
    # training.
    dow = target_date.weekday()
    sw8 = [float(history[d]) for d in reversed(sorted_dates) if d.weekday() == dow][:8]
    features["dow_mean_8w"] = statistics.fmean(sw8) if len(sw8) >= 2 else None
    features["dow_std_8w"] = statistics.stdev(sw8) if len(sw8) >= 2 else None

    # Trend ratio; null when either input is missing or 0/inf-denominated.
    rm7, rm28 = features["roll_mean_7"], features["roll_mean_28"]
    if rm7 is None or rm28 in (None, 0):
        features["trend_7_28"] = None
    else:
        trend = rm7 / rm28
        features["trend_7_28"] = None if math.isinf(trend) else trend

    # --- Calendar additions -------------------------------------------------
    weekday = target_date.weekday()
    features["week_of_year"] = target_date.isocalendar()[1]
    features["is_month_end"] = int(
        (target_date + datetime.timedelta(days=1)).month != target_date.month
    )
    features["days_to_month_end"] = (
        calendar.monthrange(target_date.year, target_date.month)[1] - target_date.day
    )

    is_weekend = bool(features.get("is_weekend", weekday >= 5))
    is_holiday = _is_public_holiday(target_date, holidays)
    features["is_holiday"] = int(is_holiday)
    features["is_weekend"] = int(is_weekend)
    features["is_weekend_or_holiday"] = int(is_weekend or is_holiday)
    features["is_weekend_holiday"] = int(is_weekend and is_holiday)

    # --- Reservations (fs-2026.09.2, Section 3.10) ---------------------------
    # Training contract: reservations booked strictly BEFORE the visit day
    # starts; the day-start boundary is applied at ingestion, so any row for
    # ``target_date`` here is by construction knowable at forecast time (the
    # real-time information boundary — no leak). Locations without pre-day
    # bookings keep nulls, never zeros (no booking data ≠ zero bookings).
    rsv_rows = _reservations_for_location(db, location_id, target_date=target_date)
    if rsv_rows:
        visitor_sums = [
            int(r.reserved_visitors) for r in rsv_rows if r.reserved_visitors is not None
        ]
        features["rsv_visitors"] = float(sum(visitor_sums)) if visitor_sums else None
        features["rsv_count"] = int(sum(int(r.reserved_count or 0) for r in rsv_rows))
        # Ratio denominator: the store's first non-null ``roll_mean_28`` in the
        # training frame — with positional windows that is the mean of its
        # first 7 observed values (GroupBy.first skips the NaN leading rows).
        denom = statistics.fmean(vals[:7]) if n >= 7 else None
        rv = features["rsv_visitors"]
        if rv is None or denom in (None, 0):
            features["rsv_visitors_ratio"] = None
        else:
            ratio = rv / denom
            features["rsv_visitors_ratio"] = None if math.isinf(ratio) else ratio
    else:
        features["rsv_visitors"] = None
        features["rsv_count"] = None
        features["rsv_visitors_ratio"] = None

    return FeatureVector(
        location_id=location_id,
        target_date=target_date,
        as_of=as_of,
        features=features,
        history_days=fv.history_days,
    )
