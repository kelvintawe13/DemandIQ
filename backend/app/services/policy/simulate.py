"""Policy simulation — forecast to preparation decision (ARCHITECTURE.md §4).

Four transparent rules (all visitor-equivalents, never kg):
* ``historical_average`` — mean of last *n* comparable days.
* ``point_forecast`` — ``P = D̂``.
* ``uncertainty_aware`` — ``P = Q(q)``, the upper ``q`` quantile of the
  interval (here approximated as ``lower + q*(upper-lower)``).
* ``risk_aware`` — the Section 3.17 policy that balances surplus against
  unmet demand from an explicit cost ratio rather than a coverage level. Its
  preparation quantity is ``D̂`` plus the newsvendor critical fractile of the
  error distribution, ``weight / (1 + weight)``, where ``weight`` is the cost
  of leaving one customer unserved relative to one leftover portion.

Exposures reported for each policy (both sides so that trade is visible):
* ``SR = max(0, P − D̂)`` — exposure relative to the forecast. Always defined.
* ``PS = max(0, P − D)`` — exposure against realised demand.
* ``UD = max(0, D − P)`` — unmet demand.
* ``planned_shortfall = max(0, D̂ − P)`` — shortfall against the forecast, so a
  policy can be compared on both sides even before the day happens.

Raising ``q`` reduces ``UD`` and increases ``PS``; the API returns both sides.

``PS`` and ``UD`` are measured against **realised** demand, per §3.19 of the
proposal, so they are ``None`` whenever no observation exists for the target day
— a future date, most importantly. The alternative (substituting the forecast
for the unknown outcome) made ``PS`` an exact duplicate of ``SR`` and reported a
model output as if it were an observed result, which is precisely the
unobserved/simulated conflation the proposal's evidence boundary forbids. For a
future day the caller gets ``SR`` and ``planned_shortfall``; the realised pair
appears once the day has data.

``historical_average`` needs a lookback window with at least one observation.
When the window is empty the rule has nothing to average, so the policy is
reported as **not computable**: ``prepared_qty`` — and every exposure derived
from it — is ``None``, and ``unavailable_reason`` says why. It used to return a
hardcoded ``20.0``, which put an invented quantity in front of an operator under
the name of a measured average. For a site with no recorded demand the honest
answer is "no basis", not a number to prepare against.

(The research pipeline in ``model/src/diq_model/policy/simulate.py`` keeps a
number for its offline table by falling back to the global pre-window mean and
emitting a ``RuntimeWarning``. That is a deliberate difference, not a drift: a
batch evaluation must score every policy on the same rows, and the borrowed
value is announced as a warning rather than presented as that site's average.)
"""

from __future__ import annotations

import datetime
import statistics
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.forecast import Forecast
from app.models.location import DemandObservation


@dataclass(frozen=True, slots=True)
class PolicyResult:
    """Per-policy simulation outcome (visitor-equivalent units)."""

    policy: str
    # None when the rule had nothing to measure (see ``unavailable_reason``);
    # every exposure below is derived from it, so they are absent together.
    prepared_qty: float | None
    surplus_sr: float | None
    # None when the target day has no observed demand yet (e.g. tomorrow).
    surplus_ps: float | None
    unmet_ud: float | None
    surplus_days: int
    unmet_days: int
    quantile: float | None
    lookback_days: int | None
    # Cost ratio the risk-aware rule used (stock-out / leftover), or None.
    risk_weight: float | None = None
    # Actual demand the realised metrics were measured against, or None.
    realised_demand: float | None = None
    # max(0, D̂ − P): the forecast-relative shortfall, or None with no quantity.
    planned_shortfall: float | None = None
    # Why ``prepared_qty`` is None (e.g. ``no_history``), else None.
    unavailable_reason: str | None = None


def _quantile_upper(lower: float, upper: float, q: float) -> float:
    """Approximate upper quantile within interval.

    Real quantile comes from a full predictive distribution; here we
    interpolate linearly between lower and upper for determinism.
    """
    q = max(0.5, min(0.99, q))
    return float(round(lower + q * (upper - lower), 2))


# Half-width of a 90% central interval in standard deviations. The stored
# interval is the ACI 90% bound, so (upper - lower) / 2 is the 0.95 quantile of
# the absolute error under a symmetric error law.
_Z_90_HALF_WIDTH = 1.6448536269514722

# Cost of one unmet customer relative to one leftover portion, used when the
# caller does not supply ``risk_weight``.
DEFAULT_RISK_WEIGHT = 2.0

# ``unavailable_reason`` for a rule whose inputs are absent. Only
# ``historical_average`` can hit this today: every other rule is derived from the
# forecast the caller supplied, whereas this one needs observed history.
NO_HISTORY = "no_history"


def _risk_aware_qty(
    point: float, lower: float, upper: float, weight: float = DEFAULT_RISK_WEIGHT
) -> float:
    """Newsvendor preparation quantity (Section 3.17).

    With ``weight`` = the cost of one unserved customer relative to one leftover
    portion (the leftover carries unit cost 1 by normalisation), the quantity
    that minimises expected total cost is the ``weight / (1 + weight)`` quantile
    of the forecast error. That is the balance between surplus and unmet demand
    the brief asks for — not a coverage level, which is what distinguishes this
    rule from ``uncertainty_aware``.

    The API stores one interval per forecast day rather than an error sample, so
    the quantile is derived from the interval half-width: the 90% ACI bound
    makes ``half`` the 0.95 quantile of |error|, and the normal-implied ratio
    ``z_q / z_0.95`` rescales it to the requested level. This is an
    APPROXIMATION and is labelled as such in the API response (``basis``); the
    research pipeline computes the same quantile exactly from the empirical
    calibration residuals (``model/src/diq_model/policy/simulate.py``).
    """
    if weight <= 0:
        raise ValueError("risk weight must be > 0")
    level = weight / (1.0 + weight)
    half = max(0.0, (float(upper) - float(lower)) / 2.0)
    # normal quantile at ``level``, scaled from the interval's own 0.95 quantile
    z = statistics.NormalDist().inv_cdf(level)
    return float(round(float(point) + half * (z / _Z_90_HALF_WIDTH), 2))


def _historical_average_qty(
    db: Session,
    location_id: uuid.UUID,
    target_date: datetime.date,
    lookback_days: int = 28,
) -> float | None:
    """Mean of last ``lookback_days`` comparable days before ``target_date``.

    Returns ``None`` when the window holds no observation — there is no average
    to take, and inventing one would put a fabricated quantity in front of an
    operator (see the module docstring).
    """
    start = target_date - datetime.timedelta(days=lookback_days)
    rows = db.scalars(
        select(DemandObservation)
        .where(
            DemandObservation.location_id == location_id,
            DemandObservation.date >= start,
            DemandObservation.date < target_date,
        )
        .order_by(DemandObservation.date.asc())
    ).all()
    vals = [r.demand_qty for r in rows]
    if not vals:
        return None
    return float(round(statistics.mean(vals), 2))


def simulate_policies(
    db: Session,
    *,
    location_id: uuid.UUID,
    target_date: datetime.date,
    point: float,
    lower: float,
    upper: float,
    policies: list[str] | None = None,
    quantile: float = 0.80,
    lookback_days: int = 28,
    risk_weight: float = DEFAULT_RISK_WEIGHT,
    realised_demand: int | float | None = None,
) -> list[PolicyResult]:
    """Simulate the four policies for a single forecast day.

    Args:
        db: Database session (used for historical averaging and to resolve
            realised demand when ``realised_demand`` is None).
        location_id: Location being evaluated.
        target_date: Day the forecast targets.
        point: Point forecast ``D̂``.
        lower: Interval lower bound.
        upper: Interval upper bound.
        policies: Subset of ``['historical_average','point_forecast',
            'uncertainty_aware','risk_aware']`` to simulate. ``None`` means all
            four.
        quantile: Quantile for the uncertainty-aware policy (0.5-0.99).
        lookback_days: Window for the historical-average policy.
        risk_weight: Cost of one unmet customer relative to one leftover
            portion, for the risk-aware policy.
        realised_demand: Actual demand ``D`` if already known; when ``None``,
            looks up ``demand_observations`` for ``target_date``.

    Returns:
        List of :class:`PolicyResult` in the requested policy order.
    """
    if policies is None:
        policies = [
            "historical_average",
            "point_forecast",
            "uncertainty_aware",
            "risk_aware",
        ]  # noqa: E501

    # Resolve realised demand if not supplied. ``None`` means "not observed yet"
    # and is preserved as such — never back-filled from the forecast.
    if realised_demand is None:
        try:
            obs: DemandObservation | None = db.scalar(
                select(DemandObservation).where(
                    DemandObservation.location_id == location_id,
                    DemandObservation.date == target_date,
                )
            )
        except Exception:  # pragma: no cover - defensive (mocked session)
            obs = None
        # Only a real row counts as an observation. A mocked session hands back
        # a MagicMock, and ``float()`` on one quietly yields 1.0 — a fabricated
        # realised demand, which is the exact failure this module guards against.
        realised_demand = float(obs.demand_qty) if isinstance(obs, DemandObservation) else None

    d_hat = float(point)
    d_val: float | None = float(realised_demand) if realised_demand is not None else None

    results: list[PolicyResult] = []
    for kind in policies:
        rw_val: float | None = None
        if kind == "historical_average":
            p = _historical_average_qty(db, location_id, target_date, lookback_days)
            q_val: float | None = None
            lb_n: int | None = lookback_days
        elif kind == "point_forecast":
            p = float(d_hat)
            q_val = None
            lb_n = None
        elif kind == "uncertainty_aware":
            p = _quantile_upper(lower, upper, quantile)
            q_val = float(quantile)
            lb_n = None
        elif kind == "risk_aware":
            p = _risk_aware_qty(d_hat, lower, upper, risk_weight)
            q_val = None
            lb_n = None
            rw_val = float(risk_weight)
        else:
            # Unknown policy — skip so that caller-supplied filtering does not 500.
            continue

        if p is None:
            # The rule had nothing to measure. Report the policy as not
            # computable instead of inventing a quantity: SR, planned_shortfall,
            # PS and UD are all derived from P, so they are absent together.
            results.append(
                PolicyResult(
                    policy=kind,
                    prepared_qty=None,
                    surplus_sr=None,
                    surplus_ps=None,
                    unmet_ud=None,
                    surplus_days=0,
                    unmet_days=0,
                    quantile=q_val,
                    lookback_days=lb_n,
                    risk_weight=rw_val,
                    realised_demand=d_val,
                    planned_shortfall=None,
                    unavailable_reason=NO_HISTORY,
                )
            )
            continue

        p = float(round(p, 2))
        surplus_sr = max(0.0, p - d_hat)
        planned_shortfall = max(0.0, d_hat - p)
        if d_val is not None:
            surplus_ps: float | None = max(0.0, p - d_val)
            unmet_ud: float | None = max(0.0, d_val - p)
        else:
            # No outcome to measure against: report nothing rather than a copy of SR.
            surplus_ps = None
            unmet_ud = None

        results.append(
            PolicyResult(
                policy=kind,
                prepared_qty=p,
                surplus_sr=float(round(surplus_sr, 2)),
                surplus_ps=float(round(surplus_ps, 2)) if surplus_ps is not None else None,
                unmet_ud=float(round(unmet_ud, 2)) if unmet_ud is not None else None,
                surplus_days=1 if (surplus_ps is not None and surplus_ps > 0) else 0,
                unmet_days=1 if (unmet_ud is not None and unmet_ud > 0) else 0,
                quantile=q_val,
                lookback_days=lb_n,
                risk_weight=rw_val,
                realised_demand=d_val,
                planned_shortfall=float(round(planned_shortfall, 2)),
            )
        )

    return results


def simulate_period(
    db: Session,
    *,
    location_id: uuid.UUID,
    forecasts: list[Forecast],
    policies: list[str] | None = None,
    quantile: float = 0.80,
    lookback_days: int = 28,
    risk_weight: float = DEFAULT_RISK_WEIGHT,
) -> dict[str, dict[str, float | int | str | None]]:
    """Aggregate simulation over a period of forecasts.

    Args:
        db: Database session.
        location_id: Location.
        forecasts: Forecasts in the period (each with point/lower/upper).
        policies: Policies to simulate.
        quantile: Quantile for uncertainty-aware.
        lookback_days: Lookback for historical average.
        risk_weight: Cost ratio for the risk-aware policy.

    Returns:
        Mapping ``policy -> {prepared_total, surplus_sr, surplus_ps, unmet_ud,
        surplus_days, unmet_days, prepared_days, realised_days,
        planned_shortfall, unavailable_reason}`` aggregated across the period.

        ``surplus_ps``/``unmet_ud`` are ``None`` when no day in the period has
        observed demand to measure against. ``prepared_total``,
        ``surplus_sr`` and ``planned_shortfall`` are ``None`` when the rule
        could not be computed on *any* day — a ``0.0`` there would read as
        "prepare nothing", which is a different (and wrong) statement.
    """
    agg: dict[str, dict[str, float | int | str | None]] = {}
    for fc in forecasts:
        per_day = simulate_policies(
            db,
            location_id=location_id,
            target_date=fc.target_date,
            point=float(fc.point_forecast),
            lower=float(fc.lower_bound),
            upper=float(fc.upper_bound),
            policies=policies,
            quantile=quantile,
            lookback_days=lookback_days,
            risk_weight=risk_weight,
        )
        for r in per_day:
            entry = agg.setdefault(
                r.policy,
                {
                    "prepared_total": 0.0,
                    "surplus_sr": 0.0,
                    "planned_shortfall": 0.0,
                    "surplus_ps": 0.0,
                    "unmet_ud": 0.0,
                    "surplus_days": 0,
                    "unmet_days": 0,
                    "prepared_days": 0,
                    "realised_days": 0,
                    "unavailable_reason": None,
                },
            )
            if r.prepared_qty is not None:
                entry["prepared_total"] = float(entry["prepared_total"] or 0.0) + r.prepared_qty
                entry["surplus_sr"] = float(entry["surplus_sr"] or 0.0) + float(r.surplus_sr or 0.0)
                entry["planned_shortfall"] = float(entry["planned_shortfall"] or 0.0) + float(
                    r.planned_shortfall or 0.0
                )
                entry["prepared_days"] = int(entry["prepared_days"] or 0) + 1
            else:
                # Remember why, so the caller can say "no basis" rather than 0.
                entry["unavailable_reason"] = r.unavailable_reason
            if r.surplus_ps is not None and r.unmet_ud is not None:
                entry["surplus_ps"] = float(entry["surplus_ps"] or 0.0) + r.surplus_ps
                entry["unmet_ud"] = float(entry["unmet_ud"] or 0.0) + r.unmet_ud
                entry["surplus_days"] = int(entry["surplus_days"] or 0) + r.surplus_days
                entry["unmet_days"] = int(entry["unmet_days"] or 0) + r.unmet_days
                entry["realised_days"] = int(entry["realised_days"] or 0) + 1
    # Round aggregated floats; realised metrics stay None with no observed days.
    for v in agg.values():
        if not v["prepared_days"]:
            # Not one day had what this rule needs. Report no quantity: summing
            # to 0.0 would read as "prepare nothing", which is not what happened.
            v["prepared_total"] = None
            v["surplus_sr"] = None
            v["planned_shortfall"] = None
        else:
            for k in ("prepared_total", "surplus_sr", "planned_shortfall"):
                v[k] = float(round(float(v[k] or 0.0), 2))
        if not v["realised_days"]:
            v["surplus_ps"] = None
            v["unmet_ud"] = None
        else:
            v["surplus_ps"] = float(round(float(v["surplus_ps"] or 0.0), 2))
            v["unmet_ud"] = float(round(float(v["unmet_ud"] or 0.0), 2))
    return agg
