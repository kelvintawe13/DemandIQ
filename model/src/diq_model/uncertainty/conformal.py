"""Adaptive conformal inference (Section 3.16; Gibbs & Candès, 2021).

Restaurant demand is non-stationary, so a standard exchangeable conformal
procedure is not safe. ACI tracks realised miscoverage online and updates a
single step-size parameter:

    alpha_{t+1} = clip(alpha_t + gamma * (target_miscoverage - 1{y_t outside interval_t}), 0, 1)

Intervals widen after a run of surprises and tighten once the series settles.

Boundary discipline: the conformal loop is *online* - when scoring day t the
interval is built from alpha_t and a residual quantile that only sees days
strictly before t. The test window is never used to calibrate.

Stratified variant (Mondrian ACI): one ACI per PREDICTED demand level, so a
heavy-tailed bucket calibrates on its own scale instead of inheriting the
quiet-day quantile. Bucketing by prediction (not the realised value) keeps
the procedure usable at forecast time without seeing the truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class ACIState:
    """Mutable state of the online ACI loop."""

    alpha: float = 0.10  # miscoverage level; starts at the target
    t: int = 0


@dataclass
class CoverageByLevel:
    """Coverage broken out by demand level (MODEL_LIFECYCLE.md section 5).

    An interval well calibrated on average can still fail systematically on
    busy days; this decomposition exposes that.
    """

    bins: list[tuple[float, float]] = field(
        default_factory=lambda: [(0, 15), (15, 30), (30, 60), (60, np.inf)]
    )

    def coverage(self, y: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> dict[str, float]:
        out: dict[str, float] = {}
        for low, high in self.bins:
            mask = (y >= low) & (y < high)
            label = f"{low:g}-{high:g}" if np.isfinite(high) else f"{low:g}+"
            out[label] = float(((y >= lo) & (y <= hi))[mask].mean()) if mask.any() else np.nan
        return out


@dataclass(frozen=True)
class DemandLevels:
    """Shared demand-level bins: the same four levels used by the
    CoverageByLevel reporter, so calibration strata and reporting strata
    coincide (stratify on predicted demand, report on realised)."""

    bins: tuple[tuple[float, float], ...] = ((0, 15), (15, 30), (30, 60), (60, np.inf))

    def label(self, low: float, high: float) -> str:
        return f"{low:g}-{high:g}" if np.isfinite(high) else f"{low:g}+"

    def index_of(self, values: np.ndarray) -> np.ndarray:
        """Bin index per value.

        Negative predictions clamp into the lowest bin (a slightly negative
        forecast is a small forecast, not a data bug). Non-finite values
        raise - they would silently corrupt every downstream comparison.
        """
        values = np.asarray(values, dtype="float64")
        if not np.isfinite(values).all():
            raise ValueError("demand values must be finite for stratification")
        values = np.maximum(values, 0.0)
        idx = np.full(len(values), -1, dtype=int)
        for i, (low, high) in enumerate(self.bins):
            idx[(values >= low) & (values < high)] = i
        assert not (idx < 0).any(), "bin gaps in DemandLevels"
        return idx

    def labels(self) -> list[str]:
        return [self.label(low, high) for low, high in self.bins]


class AdaptiveConformal:
    """Online adaptive conformal intervals on the ORIGINAL visitor scale.

    Quantiles are taken over recent absolute residuals (score s = |y - yhat|),
    re-read each step from the trailing window, so the interval adapts both
    through alpha and through the residual window itself.
    """

    def __init__(
        self,
        target_coverage: float = 0.90,
        gamma: float = 0.02,
        window: int = 200,
        min_history: int = 20,
    ) -> None:
        if not 0 < target_coverage < 1:
            raise ValueError("target_coverage must be in (0, 1)")
        self.target = target_coverage
        self.gamma = gamma
        self.window = window
        self.min_history = min_history
        self.state = ACIState(alpha=1 - target_coverage)

    # ------------------------------------------------------------ internals
    def _interval_halfwidth(self, residuals: np.ndarray) -> float:
        """Conformal quantile of recent absolute residuals at level alpha."""
        if len(residuals) < self.min_history:
            return float("nan")
        alpha_eff = float(np.clip(self.state.alpha, 1e-6, 1 - 1e-6))
        q = float(np.quantile(residuals[-self.window:], 1 - alpha_eff, method="higher"))
        # Finite-sample correction for exchangeable data: (1+1/n) quantile
        # inflation. ACI's guarantee is online, but this keeps early windows
        # honest instead of over-tight.
        n = min(len(residuals), self.window)
        return float(q * (1 + 1 / n))

    # ----------------------------------------------------------------- loop
    def calibrate_online(
        self,
        y_true: np.ndarray,
        y_pred: np.ndarray,
        warmup_true: np.ndarray | None = None,
        warmup_pred: np.ndarray | None = None,
    ) -> list[tuple[float, float]]:
        """Run the online loop; return one interval per scored day.

        At step t the interval uses only residuals from steps < t (plus the
        optional warmup block from the validation window) and alpha_t. The
        pair (y_t, yhat_t) updates the state AFTER scoring day t.
        """
        y_true = np.asarray(y_true, dtype="float64")
        y_pred = np.asarray(y_pred, dtype="float64")
        if y_true.shape != y_pred.shape:
            raise ValueError("y_true/y_pred shape mismatch")

        residuals: list[float] = []
        if warmup_true is not None:
            wt = np.asarray(warmup_true, dtype="float64")
            wp = np.asarray(warmup_pred, dtype="float64")
            residuals.extend(np.abs(wt - wp).tolist())

        intervals: list[tuple[float, float]] = []
        misses = 0
        for t in range(len(y_true)):
            # Only the trailing window can affect the quantile, so materialise
            # just that slice. Converting the whole growing list every step is
            # O(n^2) and dominated this stage (476s for a 58k-row window); the
            # window slice keeps it O(n * window) with identical numbers.
            half = self._interval_halfwidth(np.asarray(residuals[-self.window :]))
            if np.isnan(half):
                intervals.append((np.nan, np.nan))
            else:
                intervals.append((y_pred[t] - half, y_pred[t] + half))
            missed = not (intervals[-1][0] <= y_true[t] <= intervals[-1][1])
            misses += missed

            # Gibbs & Candès update, on the ORIGINAL scale (1 = miss).
            self.state.alpha += self.gamma * ((1 - self.target) - float(missed))
            self.state.alpha = float(np.clip(self.state.alpha, 1e-6, 1 - 1e-6))
            self.state.t += 1
            residuals.append(abs(y_true[t] - y_pred[t]))

        self.last_misses_ = misses
        return intervals

    # ------------------------------------------------------------ reporting
    @staticmethod
    def empirical_coverage(y_true, lo, hi) -> float:
        """Share of realised demand inside the interval (NaN intervals excluded)."""
        y_true = np.asarray(y_true, dtype="float64")
        lo = np.asarray(lo, dtype="float64")
        hi = np.asarray(hi, dtype="float64")
        valid = ~np.isnan(lo)  # warmup days without an interval are excluded
        if not valid.any():
            return float("nan")
        inside = (y_true[valid] >= lo[valid]) & (y_true[valid] <= hi[valid])
        return float(inside.mean())

    @staticmethod
    def mean_interval_width(lo, hi) -> float:
        lo = np.asarray(lo, dtype="float64")
        hi = np.asarray(hi, dtype="float64")
        valid = ~np.isnan(lo)
        return float((hi[valid] - lo[valid]).mean()) if valid.any() else float("nan")


class StratifiedAdaptiveConformal:
    """Mondrian ACI: one AdaptiveConformal per PREDICTED demand level.

    Why stratify on the PREDICTION, not the realised value: at forecast time
    only the point prediction is known, so the bucket must be computable
    without the day's truth (no-self-leak by construction). Each stratum
    keeps its own alpha, residual window and warmup slice, so a heavy-tailed
    bucket calibrates on its own scale instead of inheriting the quiet-day
    quantile - the fix for the 60+ coverage collapse under one global
    quantile.

    Thin buckets (fewer than ``min_bucket`` pre-window residuals in the
    stratum) cannot support a quantile; they fall back to the GLOBAL ACI's
    half-width rather than emitting a garbage interval. The global ACI runs
    over all days regardless, so the fallback is always available after
    warmup.
    """

    def __init__(
        self,
        target_coverage: float = 0.90,
        gamma: float = 0.02,
        window: int = 200,
        min_history: int = 20,
        min_bucket: int = 30,
    ) -> None:
        self.levels = DemandLevels()
        self.min_bucket = min_bucket
        self.global_aci = AdaptiveConformal(
            target_coverage=target_coverage, gamma=gamma,
            window=window, min_history=min_history,
        )
        self.strata: list[AdaptiveConformal] = [
            AdaptiveConformal(
                target_coverage=target_coverage, gamma=gamma,
                window=window, min_history=min_history,
            )
            for _ in self.levels.bins
        ]

    def calibrate_online(
        self,
        y_true: np.ndarray,
        y_pred: np.ndarray,
        warmup_true: np.ndarray | None = None,
        warmup_pred: np.ndarray | None = None,
    ) -> tuple[list[tuple[float, float]], list[str]]:
        """Online stratified loop; returns (intervals, bucket labels).

        Same discipline as the global loop: when scoring day t, the day's
        stratum sees only residuals from days < t of the SAME stratum (plus
        that stratum's warmup block from the train validation tail). The
        pair updates the stratum's state AFTER scoring day t. The global ACI
        runs in parallel over all days and provides the thin-bucket
        fallback.
        """
        y_true = np.asarray(y_true, dtype="float64")
        y_pred = np.asarray(y_pred, dtype="float64")
        if y_true.shape != y_pred.shape:
            raise ValueError("y_true/y_pred shape mismatch")

        n_bins = len(self.levels.bins)
        buckets = self.levels.index_of(y_pred)

        # Per-stratum warmup residual blocks (prediction-bucketed).
        strata_warmup: list[list[float]] = [[] for _ in range(n_bins)]
        if warmup_true is not None:
            wt = np.asarray(warmup_true, dtype="float64")
            wp = np.asarray(warmup_pred, dtype="float64")
            if wt.shape != wp.shape:
                raise ValueError("warmup_true/warmup_pred shape mismatch")
            warmup_buckets = self.levels.index_of(wp)
            wr = np.abs(wt - wp)
            for i, b in enumerate(warmup_buckets):
                strata_warmup[b].append(float(wr[i]))

        # Chronological day indices per stratum.
        strata_days: list[list[int]] = [[] for _ in range(n_bins)]
        for t, b in enumerate(buckets):
            strata_days[b].append(t)

        # Global ACI over all days (fallback source).
        if warmup_true is not None:
            global_intervals = self.global_aci.calibrate_online(
                y_true, y_pred, warmup_true=wt, warmup_pred=wp
            )
        else:
            global_intervals = self.global_aci.calibrate_online(y_true, y_pred)

        # Per-stratum ACI over that stratum's own days only.
        stratum_intervals: list[dict[int, tuple[float, float]]] = []
        for b in range(n_bins):
            days = strata_days[b]
            aci = self.strata[b]
            residuals = list(strata_warmup[b])
            per_day: dict[int, tuple[float, float]] = {}
            for t in days:
                # Same window-slice fix as the global loop (O(n^2) -> O(n*window)).
                half = aci._interval_halfwidth(np.asarray(residuals[-aci.window :]))
                if np.isnan(half):
                    per_day[t] = (np.nan, np.nan)
                else:
                    per_day[t] = (y_pred[t] - half, y_pred[t] + half)
                missed = not (per_day[t][0] <= y_true[t] <= per_day[t][1])
                aci.state.alpha += aci.gamma * ((1 - aci.target) - float(missed))
                aci.state.alpha = float(np.clip(aci.state.alpha, 1e-6, 1 - 1e-6))
                aci.state.t += 1
                residuals.append(abs(y_true[t] - y_pred[t]))
            stratum_intervals.append(per_day)

        # Assemble: stratum interval when its bucket is thick enough on the
        # pre-window residuals, else the global fallback.
        intervals: list[tuple[float, float]] = []
        labels: list[str] = []
        for t in range(len(y_true)):
            b = int(buckets[t])
            thick = len(strata_warmup[b]) >= self.min_bucket
            iv = stratum_intervals[b][t]
            if thick and not np.isnan(iv[0]):
                intervals.append(iv)
                labels.append(self.levels.label(*self.levels.bins[b]))
            else:
                intervals.append(global_intervals[t])
                labels.append("global-fallback")
        return intervals, labels

    # ------------------------------------------------------------ reporting
    def coverage_by_bucket(
        self, y_true: np.ndarray, lo: np.ndarray, hi: np.ndarray, y_pred: np.ndarray
    ) -> dict[str, float]:
        """Coverage per PREDICTED stratum (the calibration view)."""
        y_true = np.asarray(y_true, dtype="float64")
        lo = np.asarray(lo, dtype="float64")
        hi = np.asarray(hi, dtype="float64")
        y_pred = np.asarray(y_pred, dtype="float64")
        buckets = self.levels.index_of(y_pred)
        out: dict[str, float] = {}
        for b, (low, high) in enumerate(self.levels.bins):
            mask = buckets == b
            valid = mask & ~np.isnan(lo)
            label = self.levels.label(low, high)
            out[label] = (
                float(((y_true[valid] >= lo[valid]) & (y_true[valid] <= hi[valid])).mean())
                if valid.any() else np.nan
            )
        return out
