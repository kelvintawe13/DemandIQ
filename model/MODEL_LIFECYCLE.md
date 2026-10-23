# DemandIQ — Model Lifecycle, End to End

From raw demand rows to a preparation decision, and back again through
monitoring. This is the product/lifecycle **contract**.

> **Scope.** The implemented, authoritative description of the offline
> training pipeline is **[PIPELINE.md](PIPELINE.md)**; where the two differ,
> PIPELINE.md governs the training run. The divergences are called out inline
> below rather than left implicit.

## 0. The target

`y = demand_qty` — covers (or visitor-equivalent transactions) for one location
on one service day. Horizon: **one day ahead** by default, extended to 30 days
for planning views by recursive forecasting with widening intervals.

The distribution is strongly right-skewed, so RMSLE sits alongside MAE and
RMSE, and models train on `log1p(y)` with predictions inverted at serving time.

## 1. Data preparation

```
demand_observations ──► clean ──► feature build ──► chronological split
```

- Drop negative and impossible values; reconcile duplicate `(location, date)`
  rows to the most recent submission. *(Intended for the live ingestion layer;
  the offline training pipeline deliberately does **not** clean — it audits and
  refuses. See [PIPELINE.md §4.3](PIPELINE.md).)*
- Flag locations below 80% daily coverage — they are still ingested, but they
  are reported separately in evaluation so sparse sites cannot quietly inflate
  or deflate a headline metric.
- Never impute the target. A missing day is missing, not zero: a closed
  restaurant and a restaurant with no customers are different facts.

## 2. Features

Four groups, built with an explicit `as_of` boundary (see
`backend/ARCHITECTURE.md` §2): historical lags and rolling statistics, calendar
effects, static location attributes, and supplementary reservation signals.

*(Implemented: the offline training frame has **three** of these groups —
historical, calendar and reservation, 26 columns at `fs-2026.09.2`. Static
location attributes belong to the product schema and are not present in the
Recruit training frame. See [PIPELINE.md §5](PIPELINE.md).)*

The leakage rule is absolute: **a feature may only read observations strictly
before the target date.** Rolling windows use a shifted series. The feature job
fails loudly rather than emitting a window that touches the target day.

## 3. Validation

Chronological only. Random shuffling would let the model learn from days that
had not happened yet.

```
fold 1  train ──────────┤ test ──┤
fold 2  train ──────────────────┤ test ──┤
fold 3  train ──────────────────────────┤ test ──┤
```

The validation tail of the training period selects hyperparameters; expanding-origin
folds are used for evaluation, not selection.

*(Implemented: a bounded eight-configuration search scored on the validation
tail — the last 10% of the training window — selects the LightGBM
hyperparameters; trial 0 is the documented default, and every trial is
persisted. Early stopping uses the same tail. The six-fold rolling-origin
backtest is opt-in **evaluation**, not selection. See [PIPELINE.md
§6–§7](PIPELINE.md).)*

A final held-out window, untouched during development, produces the numbers that
appear in the registry. That window is never used for feature selection, tuning,
policy calibration or conformal calibration.

## 4. Candidates and baselines

| Model | Role |
| --- | --- |
| Historical average | Floor. If nothing beats it, complexity is not justified |
| Day-of-week average | Second floor, captures the strongest seasonal signal |
| Linear regression | Interpretable reference |
| Random Forest | Non-linear reference |
| XGBoost | Gradient-boosting reference |
| **LightGBM** | Primary — fast on wide categorical tabular data |
| **CatBoost** | Primary — native categoricals, less tuning-sensitive |

*(Implemented role names differ: the **baselines** are `naive`,
`seasonal_naive`, `historical_average`, `weekday_mean` and `global_average`;
the **fallbacks** are linear regression, random forest and XGBoost; the
**primaries** are LightGBM and CatBoost. See [PIPELINE.md §7](PIPELINE.md).)*

Both primaries are kept because they fail differently; if one destabilises
during tuning, the other is a genuine fallback rather than a re-run of the same
idea. A neural model is only introduced if the boosted trees plateau and the
added complexity is demonstrably earned.

The three reference models are not decorative: the `fallbacks` stage in
`diq_model.train` fits all three on the *primary* feature frame — same rows,
same features, same chronological split, same `log1p` target — and persists
`fallback_table.parquet`, so the claim that the boosted trees earn their
complexity is backed by numbers from one consistent run. A dependency that is
absent (XGBoost needs the `training` extra) is reported under `skipped` with
the reason and left out of the ranking rather than quietly dropped. Their
measured MAE travels in the serving bundle (`comparison.<name>`, role
`baseline_fallback`), so the registry shows the real comparison instead of a
figure recomputed at serving time.

## 5. Uncertainty — adaptive conformal inference

Restaurant demand is non-stationary, so a standard exchangeable conformal
procedure is not safe. The adaptive variant (Gibbs & Candès) tracks realised
miscoverage and updates a step-size parameter online:

```
α_{t+1} = clip( α_t + γ (target_miscoverage − 1{y_t ∉ interval_t}), ε, 1−ε )
```

The production intervals are **stratified (Mondrian)** by *predicted* demand
level, so a heavy-tailed bucket calibrates on its own scale — see
[PIPELINE.md §8](PIPELINE.md). Intervals widen after a run of surprises and
tighten once the series settles.
Reported for every model: **empirical coverage** against the 90% target,
**mean interval width**, and coverage broken out by demand level, because an
interval that is well calibrated on average can still fail systematically on
busy days.

## 6. Decision layer

The model's job ends at `(point, lower, upper)`. Turning that into a quantity
is a separate, deliberately transparent step:

| Policy | Rule |
| --- | --- |
| Historical average | mean of the last *n* comparable days |
| Point forecast | `P = D̂` |
| Uncertainty-aware | `P = upper × scale`, from the conformal interval |
| Risk-aware | `P = D̂ + q`, the newsvendor quantile of signed errors (`w/(1+w)`) |

*(Four policies are implemented, not three — the risk-aware policy is
`P = D̂ + q` at the `w/(1+w)` critical fractile. See [PIPELINE.md §9](PIPELINE.md).)*

Evaluated together on the same held-out window:

- `SR = max(0, P − D̂)` planning exposure
- `PS = max(0, P − D)` realised over-preparation
- `UD = max(0, D − P)` unmet demand

The quantile `q` is chosen on validation data, never on the test window. The
result is a trade-off curve shown to the user, not a single "optimal" number:
raising `q` always buys coverage with surplus.

## 7. Explainability

Global feature importance for the model card; per-forecast SHAP values stored
with the forecast. Presented as predictive attribution — a feature that moves
the prediction is not thereby a cause of demand, and the UI says so.

## 8. Promotion

A candidate reaches production only if it:

1. improves on production MAE by **≥ 3%** on the rolling-origin mean,
2. does not regress any slice by more than **15%** (including the top and
   bottom demand deciles),
3. holds empirical coverage within **88–92%**,
4. keeps inference under **300 ms** per location-day at p95,
5. has a **model card** naming the training window, feature-set version,
   hyperparameters, metrics by slice, and known weaknesses,
and is approved by a second administrator through the change-request queue.

*(These are the [EVALUATION.md](EVALUATION.md) gates; the earlier “MAE and
RMSLE / 5% segment regression” wording was a design draft and is superseded.)*

Every version keeps its artifact, hyperparameters, feature-set version and
training window, so rollback is a pointer change, not a retrain.

## 9. Monitoring after release

- **Drift:** live feature distributions compared against the training
  distribution; two signals outside band recommends a retrain.
- **Rolling accuracy:** MAE over the trailing 28 days per location.
- **Coverage:** realised interval coverage; a sustained breach triggers
  recalibration before it triggers a retrain, because the point model is often
  fine when only the intervals have drifted.
- **Cadence:** recalibrate nightly, retrain weekly, or immediately when drift
  or coverage crosses threshold.

## 10. Reproducibility

Fixed seeds, and a manifest per model version recording dataset checksums, date
ranges and evaluation configuration. Any registry row can be regenerated from
its manifest alone.

Training runs **offline** in the model package's own environment (or on
Colab/Kaggle) — never in the API container, which mounts `./model` read-only
and cannot write artifacts. Dependencies are currently ranged rather than
lockfiled; see the limitations in [PIPELINE.md §17](PIPELINE.md).

## Boundaries this project holds

- Surplus figures are **simulated exposure**, never measured food waste.
- The URBAN-WASTE dataset characterises observed waste independently and is
  **never joined row-level** to demand data or used to label it.
- Research datasets are used under their own access conditions and contain no
  customer data.
