"""Preparation-policy simulation (Sections 3.17-3.20).

Four deliberately transparent policies convert forecasts into preparation
quantities; the simulation reports BOTH exposure sides of the trade-off:

    Policy                Preparation quantity
    historical_average    mean of the last n comparable days
    point_forecast        P = D_hat
    uncertainty_aware     P = upper quantile of the conformal interval
    risk_aware            P = D_hat + newsvendor quantile of the forecast error

The risk-aware policy is the Section 3.17 "additional risk-aware policy" that
balances surplus against unmet demand. It is deliberately NOT a second version
of uncertainty_aware: the preparation quantity comes from an explicit cost
ratio, not from a coverage level. Given ``weight`` = the cost of leaving one
customer unserved relative to the cost of one leftover portion, the quantity
that minimises expected total cost is the ``weight / (1 + weight)`` quantile of
the forecast-error distribution (the newsvendor critical fractile). At
``weight = 1`` (equal costing) that quantile is the median error, so the policy
degenerates to the point forecast plus a bias correction - an honest property,
not a bug, and the reason the validation sweep is allowed to conclude that
padding never pays. Errors are signed (realised - predicted) so the correction
has a direction.

Exposures (proposal Sections 3.18-3.19), all in visitor-equivalent units:

    SR = max(0, P - D_hat)   planning exposure vs the forecast that set the policy
    PS = max(0, P - D)       realised over-preparation exposure
    UD = max(0, D - P)       unmet-demand exposure

SR is zero by construction under the point-forecast policy - which is exactly
why PS is reported alongside it. These are SIMULATED exposures, never
measured food waste; quantities are visitor-equivalents, never kilograms.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum

import numpy as np
import pandas as pd


class PolicyKind(str, Enum):
    """Names match the `policy_kind` enum in database/schema.sql."""

    historical_average = "historical_average"
    point_forecast = "point_forecast"
    uncertainty_aware = "uncertainty_aware"
    risk_aware = "risk_aware"


DEFAULT_RISK_WEIGHT = 2.0
"""Default cost of one unmet customer relative to one leftover portion.

Section 3.17 permits the risk-aware policy "if sufficient evidence supports its
inclusion", so the weight is not asserted here: ``stage_policy`` sweeps it on
the validation window against the same surplus/unmet criterion used to pick the
uncertainty multiplier, and reports the verdict either way.
"""


def newsvendor_level(weight: float) -> float:
    """Critical fractile ``w / (1 + w)`` for a stock-out cost of ``w``.

    ``weight`` is the cost of one unmet customer relative to one leftover
    portion (the leftover carries a unit cost of 1 by normalisation). A higher
    weight prices missed demand more dearly and so prepares more.
    """
    if weight <= 0:
        raise ValueError("risk weight must be > 0")
    return float(weight / (1.0 + weight))


def newsvendor_quantile(residuals: np.ndarray | pd.Series, weight: float) -> float:
    """The ``w/(1+w)`` quantile of the SIGNED forecast errors.

    Distribution-free: it reads the errors the model actually made on the
    calibration window rather than assuming a shape for them (the proposal's
    "quantile-based ... intervals" as a secondary approach, Section 3.16).
    """
    r = np.asarray(residuals, dtype="float64")
    r = r[np.isfinite(r)]
    if r.size == 0:
        raise ValueError("risk_aware policy needs a non-empty residual sample")
    return float(np.quantile(r, newsvendor_level(weight)))


@dataclass(frozen=True)
class Policy:
    """One preparation policy configuration.

    ``quantile`` (uncertainty_aware only) is the multiplier applied to the
    conformal upper bound: 1.0 prepares exactly to the bound, >1.0 pads it.
    The name is chosen on validation data by sweeping the trade-off
    (stage_policy), never on the test window.
    """

    kind: PolicyKind
    quantile: float | None = None  # uncertainty_aware multiplier
    lookback_days: int | None = None  # historical_average only
    weight: float | None = None  # risk_aware cost ratio (stock-out / leftover)

    @property
    def name(self) -> str:
        bits = [self.kind.value]
        if self.quantile is not None:
            bits.append(f"q{self.quantile:g}")
        if self.lookback_days is not None:
            bits.append(f"n{self.lookback_days}")
        if self.weight is not None:
            bits.append(f"w{self.weight:g}")
        return "-".join(bits)


@dataclass(frozen=True)
class PolicyResult:
    """Aggregated exposure for one policy over one period (schema.sql:
    policy_simulations columns)."""

    policy: str
    days: int
    prepared_total: float
    surplus_sr: float
    surplus_ps: float
    unmet_ud: float
    surplus_days: int  # days with PS > 0
    unmet_days: int  # days with UD > 0

    def as_dict(self) -> dict:
        return asdict(self)


def default_policies(
    quantile: float = 1.0,
    lookback_days: int = 28,
    risk_weight: float = DEFAULT_RISK_WEIGHT,
) -> list[Policy]:
    """The proposal policies (Sections 3.17). The uncertainty multiplier
    defaults to 1.0 (prepare exactly to the bound) and the risk weight to 2.0;
    both are tuned on validation data (Section 3.14), never on the test window.
    """
    return [
        Policy(PolicyKind.historical_average, lookback_days=lookback_days),
        Policy(PolicyKind.point_forecast),
        Policy(PolicyKind.uncertainty_aware, quantile=quantile),
        Policy(PolicyKind.risk_aware, weight=risk_weight),
    ]


def _prep_from_forecast(
    df: pd.DataFrame,
    policy: Policy,
    upper_col: str | None,
    residuals: np.ndarray | pd.Series | None = None,
) -> pd.Series:
    if policy.kind is PolicyKind.point_forecast:
        return df["prediction"].astype("float64")
    if policy.kind is PolicyKind.uncertainty_aware:
        if upper_col is None or upper_col not in df.columns:
            raise ValueError(
                "uncertainty_aware policy requires conformal upper bounds"
            )
        prep = df[upper_col].astype("float64")
        if policy.quantile is not None:
            # The multiplier tuned on VALIDATION data (stage_policy sweeps it;
            # Section 3.14: hyperparameters are chosen off the test window).
            prep = prep * float(policy.quantile)
        return prep
    if policy.kind is PolicyKind.risk_aware:
        if residuals is None:
            raise ValueError(
                "risk_aware policy requires the calibration residuals "
                "(the newsvendor quantile is read off the errors the model "
                "made before the simulation window)"
            )
        weight = DEFAULT_RISK_WEIGHT if policy.weight is None else float(policy.weight)
        # One quantile for the whole simulation, from the calibration window:
        # letting the quantile move with in-window outcomes would let the
        # policy react to demand it is supposed to be planned against.
        q = newsvendor_quantile(residuals, weight)
        return df["prediction"].astype("float64") + q
    raise ValueError(f"policy {policy} is computed from history, not forecasts")


def _prep_from_history(
    df: pd.DataFrame, visits: pd.DataFrame, policy: Policy
) -> pd.Series:
    """Static historical-average policy: per store, the mean of the last
    ``lookback_days`` demand days STRICTLY BEFORE the simulation window
    starts, held constant across the window.

    Frozen by design: the policy must not react to realised demand inside
    the evaluation window, and reading only pre-window history makes that
    boundary structural rather than conventional.

    Young stores degrade gracefully instead of crashing the simulation
    (real Recruit data has stores that opened mid-window):

    - < 7 pre-window history days: use whatever history exists (warn);
    - no pre-window history at all: fall back to the global pre-window
      mean across all stores (warn). A policy must still produce a number
      to be comparable on the same rows as the other policies.
    """
    import warnings

    if policy.lookback_days is None:
        raise ValueError("historical_average requires lookback_days")
    preps: list[pd.Series] = []
    for store_id, block in df.groupby("air_store_id"):
        window_start = block["visit_date"].min()
        hist = visits.loc[
            (visits["air_store_id"] == store_id)
            & (visits["visit_date"] < window_start),
            ["visit_date", "visitors"],
        ].sort_values("visit_date")
        if len(hist) == 0:
            global_mean = float(visits["visitors"].mean())
            warnings.warn(
                f"historical_average: store {store_id!r} has no pre-window "
                f"history; using the global mean ({global_mean:.1f})",
                RuntimeWarning,
                stacklevel=2,
            )
            prep = global_mean
        else:
            if len(hist) < 7:
                warnings.warn(
                    f"historical_average: store {store_id!r} has only "
                    f"{len(hist)} pre-window history days (< 7); "
                    "using what exists",
                    RuntimeWarning,
                    stacklevel=2,
                )
            prep = float(hist["visitors"].tail(policy.lookback_days).mean())
        preps.append(pd.Series(prep, index=block.index))
    return pd.concat(preps).reindex(df.index)


def simulate_policies(
    sim_df: pd.DataFrame,
    visits: pd.DataFrame,
    policies: list[Policy] | None = None,
    upper_col: str | None = "upper",
    residuals: np.ndarray | pd.Series | None = None,
) -> list[PolicyResult]:
    """Evaluate every policy on the same period.

    ``sim_df`` columns: air_store_id, visit_date, visitors (realised D),
    prediction (D_hat), and optionally the conformal ``upper`` bound.
    ``visits`` is the full ingested history, used only for the
    historical-average policy and only via shift(1) windows. ``residuals`` is
    the SIGNED calibration error (realised - predicted), used only by the
    risk-aware policy and only from a window that precedes ``sim_df``.
    """
    policies = policies or default_policies()
    results: list[PolicyResult] = []
    d_hat = sim_df["prediction"].astype("float64")
    d = sim_df["visitors"].astype("float64")

    for policy in policies:
        if policy.kind is PolicyKind.historical_average:
            prep = _prep_from_history(sim_df, visits, policy)
        else:
            prep = _prep_from_forecast(sim_df, policy, upper_col, residuals)

        sr = np.clip(prep - d_hat, 0, None)
        ps = np.clip(prep - d, 0, None)
        ud = np.clip(d - prep, 0, None)
        results.append(
            PolicyResult(
                policy=policy.name,
                days=int(len(sim_df)),
                prepared_total=round(float(prep.sum()), 2),
                surplus_sr=round(float(sr.sum()), 2),
                surplus_ps=round(float(ps.sum()), 2),
                unmet_ud=round(float(ud.sum()), 2),
                surplus_days=int((ps > 0).sum()),
                unmet_days=int((ud > 0).sum()),
            )
        )
    return results


def tradeoff_table(results: list[PolicyResult]) -> pd.DataFrame:
    """One row per policy: the trade-off the UI must show, not hide."""
    rows = [r.as_dict() for r in results]
    df = pd.DataFrame(rows)
    df["ps_per_day"] = (df["surplus_ps"] / df["days"]).round(2)
    df["ud_per_day"] = (df["unmet_ud"] / df["days"]).round(2)
    return df
