# DemandIQ — Pipeline: Data, Model, and Training

**This is the source of truth for how the model is built.** One document, from
the raw files to a served forecast: what the data is and why it is never
"cleaned", how features are built without leakage, how the chronological split
is drawn, how each model is trained, how uncertainty and the decision layer
work, and how to prove a run is real.

It replaces four earlier documents that each covered a slice of this story
(`HOW_IT_WORKS.md`, `TRAINING.md`, `IMPLEMENTATION_WALKTHROUGH.md`,
`TRAINING_JUSTIFICATION.md`); their content lives here, once. Narrower
references remain in the files this one links to:

| Document | Answers |
| --- | --- |
| [EVALUATION.md](EVALUATION.md) | How a model is judged, and the promotion gates |
| [MODEL_LIFECYCLE.md](MODEL_LIFECYCLE.md) | The design contract: candidates, uncertainty, promotion, monitoring |
| [FEATURE_SPEC.md](FEATURE_SPEC.md) | Every feature definition and the leakage rules |
| [COLAB.md](COLAB.md) / [COLAB-KAGGLE.md](COLAB-KAGGLE.md) | Running the same pipeline on remote compute |
| [CHAPTER4_DRAFT.md](CHAPTER4_DRAFT.md) | Generated: the results tables and figures from a run |

Implementation lives in `model/src/diq_model/`; the entry point is
`diq_model.train` (pipeline + CLI). Every number quoted below is from the
reference run in `model/artifacts/runs/latest/` (seed 42, test start
`2017-02-01`) and can be regenerated with `make train-model`.

### How to read this document

- **Understanding the model?** Read §1, then §4–§7 in order — that is the
  data → split → training spine.
- **Auditing or reproducing a run?** §15 (verification receipt and
  verify-it-yourself) and §16 (governance).
- **Tracing a number in a report or the UI?** §13 (what a run produces) and §14
  (how it reaches the registry).
- **Working on the code?** §3 (stage list) and §12 (one real run, stage by
  stage).
- **Just need a term or a setting?** §18 (parameter cheat-sheet) and §19
  (glossary).

---

## Contents

1. [The problem, stated honestly](#1-the-problem-stated-honestly)
2. [Run it](#2-run-it)
3. [The pipeline at a glance](#3-the-pipeline-at-a-glance)
4. [Data: loading, audit, and why there is no cleaning step](#4-data-loading-audit-and-why-there-is-no-cleaning-step)
5. [Features: turning history into signals](#5-features-turning-history-into-signals)
6. [The chronological split](#6-the-chronological-split)
7. [Training the models](#7-training-the-models)
8. [Uncertainty: adaptive conformal intervals](#8-uncertainty-adaptive-conformal-intervals)
9. [The decision layer: preparation policy](#9-the-decision-layer-preparation-policy)
10. [Explanation: SHAP, and why it is not causation](#10-explanation-shap-and-why-it-is-not-causation)
11. [Backtesting: why one window is not enough](#11-backtesting-why-one-window-is-not-enough)
12. [One real run, stage by stage](#12-one-real-run-stage-by-stage)
13. [What a run produces](#13-what-a-run-produces)
14. [From a run to the registry](#14-from-a-run-to-the-registry)
15. [Reproducibility and the audit receipt](#15-reproducibility-and-the-audit-receipt)
16. [Governance and promotion gates](#16-governance-and-promotion-gates)
17. [Known limitations](#17-known-limitations)
18. [Parameter cheat-sheet](#18-parameter-cheat-sheet)
19. [Glossary](#19-glossary)

---

## 1. The problem, stated honestly

**Input.** For one restaurant on one day: everything knowable *before that day
started*.

**Output.** Two different questions:

1. **A number** — how many visitors to expect (`point`).
2. **A range** — how wrong that number is likely to be (`lower`, `upper`).

The range is not optional. A kitchen does not order "21.4 portions"; it orders a
number it can defend, and the business consequence is asymmetric — run out and
you turn customers away, over-prepare and you throw food away. Conflating the two
into one number hides the entire decision.

**Why it is hard.**

| Property | Consequence |
| --- | --- |
| Strong weekly rhythm | Simple "same weekday last week" already works — the model must beat a strong baseline, not a weak one |
| Right-skewed demand (mean ≈ 21, max 877) | Squared-error metrics get dominated by a handful of huge days |
| Non-stationary | Today's errors are not distributed like last year's — intervals must adapt |
| Sparse per store (~829 stores, ~252k rows) | A model per store is impossible; one model must share strength |
| Missing days are *missing*, not zero | Imputing a closed day as 0 teaches "holiday ⇒ dead" |

Everything below is a response to one of those rows.

---

## 2. Run it

### The one command that matters

From the repository root:

```bash
make train-model                       # all stages + backtest + serving bundle
make train-model TRAIN_SEED=7          # any input overridable
```

`train-model` expands to exactly two processes:

```bash
# 1. train — every stage, clean rerun, with the backtest
cd model && .venv/bin/python -m diq_model.train train \
  --data-root data/recruit --checkpoints artifacts/runs/latest \
  --test-start 2017-02-01 --seed 42 --with-backtest --force

# 2. export — turn the run directory into the bundle the API loads
cd model && .venv/bin/python scripts/export_serving_bundle.py \
  --artifacts artifacts/runs/latest
```

The export reads only the files the train step left on disk, so it can be re-run
alone with `make bundle` without retraining. This is the **only** supported way a
model version reaches the registry: `POST /api/v1/models/train` answers
`501 training_unavailable` on purpose, because training belongs to the model
package, not to an HTTP request.

### Directly, from `model/`

```bash
cd model

# audit only — exits non-zero if the data fails the Section 3.7.1 checks
.venv/bin/python -m diq_model.train audit --data-root data/recruit

# full run, resumable
.venv/bin/python -m diq_model.train train \
  --data-root data/recruit --checkpoints artifacts/runs/latest \
  --test-start 2017-02-01 --seed 42

# everything, plus rolling-origin backtest and sensitivity
.venv/bin/python -m diq_model.train train --data-root data/recruit \
  --with-backtest --with-sensitivity --force

# synthetic smoke run — no download, no credentials
make smoke
```

### Checkpointing and resume

Every stage writes its metrics into `manifest.json` under the checkpoint
directory. On a re-run, a stage already recorded is **skipped** and its metrics
are loaded instead of recomputed:

```
[skip] features (checkpointed) -> {...}
[run ] lightgbm ...
```

`--force` reruns every stage regardless. This is what makes an interrupted
Colab/Colab-CLI session resume exactly where it stopped.

### CLI flags

| Flag | Default | Meaning |
| --- | --- | --- |
| `--data-root` | — (required) | Folder holding the Recruit CSVs |
| `--checkpoints` | `artifacts/runs/latest` | Checkpoint directory |
| `--test-start` | `2017-02-01` | First day of the held-out test window |
| `--seed` | `42` | Global seed |
| `--force` | off | Rerun stages already checkpointed |
| `--no-strict` | off | Subset run: block only on hard integrity failures |
| `--skip-lightgbm` | off | Stop after the `features` stage |
| `--skip-tune` | off | Train LightGBM with the documented defaults instead of the validation-tail search |
| `--tune-trials` | `8` | Ceiling on tuning configurations evaluated (the grid has 8) |
| `--with-sensitivity` | off | Add the feature-group / reservations ablation stage |
| `--with-backtest` | off | Add the rolling-origin backtest stage |
| `--backtest-folds` | `6` | Number of backtest folds |
| `--backtest-test-days` | `30` | Test-window length per fold |
| `--backtest-advance-days` | `14` | Origin advance per fold |
| `--backtest-min-train-days` | `180` | Drop folds with less training history |

---

## 3. The pipeline at a glance

`run_pipeline` executes twelve stages in a fixed order; the standard run is the
first ten. `sensitivity` and `backtest` are opt-in because each refits the
primary model many times. `tune` is bounded and only ever scores the validation
tail, so it is part of the standard run.

| # | Stage | What it does | Key outputs |
| --- | --- | --- | --- |
| 1 | `ingest` | Load + audit Recruit, persist normalised frames | row/store counts, source sha256 |
| 2 | `baseline` | Five baselines on the same chronological window | MAE/RMSE/RMSLE/MASE/Bias per baseline, `best_baseline` |
| 3 | `features` | Leakage-safe feature build + gates | rows, feature count, `fs-2026.09.2` |
| 4 | `tune` | Bounded LightGBM search scored on the validation tail | per-trial validation MAE, selected config |
| 5 | `fallbacks` | Linear / RF / XGBoost on the primary frame | MAE/RMSE/RMSLE + ranking |
| 6 | `lightgbm` | Primary model (selected config), scored against the best baseline | metrics, `best_iteration`, lift |
| 7 | `catboost` | Secondary primary, compared to LightGBM | same metrics, `mae_delta_vs_lightgbm` |
| 8 | `conformal` | Stratified adaptive conformal intervals | coverage, width, per-level breakdown |
| 9 | `policy` | Preparation-policy simulation | SR/PS/UD per policy, chosen scale |
| 10 | `shap` | Per-forecast TreeSHAP contributions | top features, additivity error |
| 11 | `sensitivity` *(opt-in)* | Leave-one-group-out + no-reservations ablation | MAE delta per group |
| 12 | `backtest` *(opt-in)* | 6-fold rolling-origin refits | per-fold + mean/std, coverage |

The dataset boundary, in one picture — arrows are data flow:

```
RAW DATA (--data-root, default data/recruit)
  air_visit_data.csv · air_store_info.csv · date_info.csv · air_reserve.csv
  → ① ingest  → load + audit (§3.7.1). Nothing trains until this passes.
  → ② baseline → five simple rules; best_baseline = the bar.
  → ③ features → leakage-safe frame, version fs-2026.09.2, gates fail loudly.
  → ④ tune     → bounded search on the train window's tail (test never seen).
  → ⑤–⑦ models → fallbacks · LightGBM · CatBoost (same frame, same split).
  → ⑧ conformal → stratified adaptive intervals.
  → ⑨ policy   → forecast → preparation quantity (SR/PS/UD).
  → ⑩ shap     → per-forecast attribution (stored, not recomputed).
  → ⑪/⑫ opt-in → sensitivity · rolling-origin backtest.
  → export_serving_bundle.py → serving_bundle.joblib → API registry.
```

---

## 4. Data: loading, audit, and why there is no cleaning step

### 4.1 What loading does

Input is the Recruit Restaurant Visitor Forecasting dataset (Kaggle slug
`recruit-restaurant-visitor-forecasting`), loaded from `--data-root`:

| File | Contents |
| --- | --- |
| `air_visit_data.csv` | Demand rows: store × visit date × visitors |
| `air_store_info.csv` | Static store attributes |
| `date_info.csv` | Japanese holiday calendar |
| `air_reserve.csv` | Reservation logs (partial coverage — 314 of 821 stores) |

`data/load.py` reads the CSVs and **pins dtypes at read time**:

```
air_visit_data.csv → air_store_id (string), visit_date (datetime), visitors (int64)
air_store_info.csv → air_store_id, air_genre_name, air_area_name (string)
date_info.csv      → calendar_date (datetime), holiday_flg (int8)
air_reserve.csv    → air_store_id, visit_datetime, reserve_datetime, reserve_visitors
```

**Why type at the boundary.** If `visitors` arrives as a float, or a date as a
string, the bug surfaces three stages later inside a rolling window where it is
nearly impossible to trace. Typing at the boundary means the rest of the
pipeline can assume the shape and never re-guess it. `air_reserve.csv` is the
only optional file — it is missing for many stores, so the loader tolerates its
absence and returns an empty frame rather than failing.

Audited figures are pinned in `diq_model/constants.py` and checked against
reality: **252,108** visit rows, **829** stores, dates `2016-01-01` →
`2017-04-22`, **92,378** reservation rows.

### 4.2 The audit (Section 3.7.1)

Eight read-only checks run before anything trains:

| # | Check | Question | Class |
| --- | --- | --- | --- |
| 1 | `audited_scale` | Exactly 252,108 rows and 829 stores? | reference |
| 2 | `duplicate_store_days` | Every `(store, date)` unique? | **hard** |
| 3 | `date_parse_failures` | Any unparseable `visit_date`? | **hard** |
| 4 | `visitor_value_range` | Any negative visitor counts? | **hard** |
| 5 | `identifier_consistency` | Any store in `visits` missing from `air_store_info`? | **hard** |
| 6 | `temporal_span` | Exactly `2016-01-01 … 2017-04-22`? | reference |
| 7 | `store_coverage` | Per-store observed/possible days | reported only |
| 8 | `reservation_coverage` | How many stores have booking logs | reported only |

**Hard** checks block training unconditionally. **Reference** checks compare
against the audited full-dataset figures and block only in strict mode — so a
subset run (`--no-strict`, used by `make smoke`) reports the deviation and
continues. `store_coverage` is information, not a gate: making it a gate would
fail real runs that simply have sparse stores, which is a property of the world,
not a bug.

Ingest also records the **sha256 of each source CSV**, so any later figure can be
traced to specific bytes.

### 4.3 Why there is no cleaning step — and why that is deliberate

This is the part that surprises people. **The pipeline does not repair data. It
refuses to proceed.**

There is no `dropna()`, no `fillna(0)`, no "reconcile duplicates to the most
recent submission". Instead: duplicates → **hard failure**; negative visitors →
**hard failure**; missing demand days → **left missing, permanently**; a store
with no history → **null features**, not zero features.

**Why refuse rather than repair.** Every repair is a silent assumption, and
silent assumptions destroy trust in a forecast six months later. Consider the
three most tempting repairs:

1. **`fillna(0)` on a missing demand day.** A restaurant closed for a holiday is
   not a restaurant that served zero customers. Imputing 0 writes a fake demand
   collapse into the training set, and the model learns "holiday ⇒ dead".
2. **Deduplicating by "keep the latest submission".** Whichever row you keep, you
   have silently invented a fact about a real day. If the source has a duplicate,
   the honest response is to look at why.
3. **Clipping a negative count to 0.** A negative is a data-integrity signal, not
   a number to nudge.

The one place the pipeline *does* fill is the **fallback models**, which cannot
handle NaN at all — and there it records the **training-window median per
column**, learned from the fit slice only (§7.2). That is a forced engineering
concession confined to the three comparison models, not a cleaning policy.

> `MODEL_LIFECYCLE.md §1` describes an intended reconciliation step for a live
> ingestion pipeline. The offline training pipeline implements the stricter
> version: it checks and blocks. On the real Recruit data all hard checks pass.

---

## 5. Features: turning history into signals

A model cannot see "a restaurant". It sees a row of numbers. All features fall
into three groups, and every one is knowable **before the target day starts**.

Built by `diq_model/features/build.py`, versioned as a single constant:

```
FEATURE_SET_VERSION = "fs-2026.09.2"
```

A model carries the version it was trained on; if the installed definitions
differ at predict time, the model raises a hard `RuntimeError` rather than
serving a stale artifact. `FEATURE_GROUPS` declares the groups once, and the
model's input list is derived from that declaration — so the sensitivity ablation
and the production columns cannot drift apart.

### 5.1 Historical group (12) — "what has this store been doing?"

| Feature | Definition | Why it exists |
| --- | --- | --- |
| `lag_1` | yesterday's visitors | Demand is autocorrelated |
| `lag_7` | same weekday last week | The weekly rhythm, directly |
| `lag_14` | same weekday two weeks ago | A second weekly sample |
| `roll_mean_7` | mean of `t−7 … t−1` | The current **level**, smoothed over a week |
| `roll_mean_28` | mean of `t−28 … t−1` | The stable month reference |
| `roll_std_28` | std over the same window | **Volatility** — a ±10 store is a different problem from a ±1 store |
| `roll_max_7` / `roll_max_28` | max over the window | A direct view of the recent **tail**; means hide spikes |
| `roll_p90_28` | 90th percentile, last 28 days | A robust version of the max |
| `dow_mean_8w` | mean of the last **8 same-weekday** occurrences | The strongest single seasonal signal (the model's most important feature) |
| `dow_std_8w` | std of those 8 occurrences | How *consistent* that weekday is |
| `trend_7_28` | `roll_mean_7 / roll_mean_28` | A scale-free ratio: >1 heating up, <1 cooling down |

**Why lags *and* rolling windows *and* same-weekday stats, when they overlap.**
They trade noise against recall: `lag_1` is maximally recent but a single noisy
sample; `roll_mean_28` is stable but blind to today's momentum; `dow_mean_8w`
targets the weekly pattern with 8 samples. Trees pick whichever is most
informative *for a given row* — a new store has no `lag_14` but may have `lag_1`
— so giving all three lets the model fall back gracefully. The `roll_max_*` and
`roll_p90_28` features exist because the model once had means but no direct view
of the tail, and the tail is where busy-day prediction fails.

### 5.2 Calendar group (11) — "what kind of day is it?"

`dow`, `is_weekend`, `month`, `week_of_year`, `is_holiday`, `day_before_holiday`,
`day_after_holiday`, `is_weekend_or_holiday`, `is_weekend_holiday`,
`is_month_end`, `days_to_month_end`.

**Why this group cannot leak.** These are properties of the *date*, computed from
the calendar, not from demand. `is_weekend_or_holiday` and `is_weekend_holiday`
look like restatements but encode first long weekends/festival runs, second rare
Saturdays that are *also* holidays — a tree would otherwise need two features to
express each. `days_to_month_end` exists rather than "is payday" because months
differ in length, so any fixed calendar rule drifts.

### 5.3 Reservation group (3) — "who has already booked?"

`rsv_visitors` (sum of booked visitors), `rsv_count` (number of bookings),
`rsv_visitors_ratio` (booked visitors ÷ recent mean demand).

Only three features, because reservation logs cover roughly a third of the
estate: a feature available for ~38% of stores is **supplementary** — if the
model leaned on it heavily it would be good for covered stores and broken for the
rest. `rsv_visitors_ratio` is scale-free, so it distinguishes "busy store" from
"early bookers".

### 5.4 The leakage rule, and how it is enforced

The rule, in one sentence:

> **A feature for day `t` may only read demand observations with `date < t`.**

Breaking it is catastrophic: the model learns to read the answer, validation
metrics look superb, and the model is worthless in production. Enforcement is
**structural, not conventional** — four mechanisms:

1. **`shift(1)` windows.** Rolling features are computed on a series shifted by
   one day, so `rolling(7)` covers `t−7 … t−1`. It is *arithmetically impossible*
   for the window to include `t`.
2. **A poison probe.** `assert_no_window_overlap` rebuilds the whole feature
   frame on a doctored copy where each probe row's target is replaced by
   `-999999`; if any historical feature on a probe row moves, the build **raises
   `FeatureValidationError`** and writes nothing.
3. **A row gate.** `assert_feature_rows_have_demand` rejects any feature row with
   no matching demand row.
4. **A dedicated test.** `tests/test_leakage.py` proves history flows strictly
   forward: poisoning day `T` may only change features on days **after** `T`.

Rows whose historical features are *entirely* null — the first day(s) of a
store's series — are dropped (829 rows, one per store). There is nothing to learn
from yet.

### 5.5 The reservation as-of rule, and missingness

Reservations are recorded with **two timestamps**: when the booking was made and
when the visit happens. A booking for day `t` made on `t−3` was knowable; one
made *on* `t` was not. So the as-of set is `reserve_datetime < visit_date` —
anything booked later is future information and is excluded. Stores with no
pre-day bookings keep **nulls**, never zeros: a store with no booking data is not
a store with zero bookings.

Missing history also stays null throughout. Boosted trees handle nulls natively;
imputing zero would teach the model that a young store is a dying store.

---

## 6. The chronological split

**Time-ordered only.** There is no `train_test_split`, no `KFold`, no shuffle
anywhere in the pipeline.

```
                 ┌─────────── training window ───────────┐┌── test ──┐
 timeline        │  fit (90%)            │ valid (10%)   ││          │
                 └───────────────────────┴───────────────┘└──────────┘
                                   --test-start ─────────►
```

For the default run:

| Slice | Boundary | Used for |
| --- | --- | --- |
| **fit** | first 90% of the train window | model fitting |
| **validation tail** | last 10% of the train window (chronological `quantile(0.9)` cutoff) | early stopping, conformal warmup, policy-multiplier sweep |
| **test** | `2017-02-01` → `2017-04-22` (57,866 store-days) | scored exactly once, at the end |

Recorded in the serving bundle: `train_start 2016-01-01`, `train_end
2017-01-31`, `test_start 2017-02-01`, `test_end 2017-04-22`, `test_rows 57,866`.

> **EVALUATION.md vs the implementation.** [EVALUATION.md](EVALUATION.md) states
> the general protocol as train up to `T−90`, validation `T−90 → T−30`, and a
> final 30-day test. The implementation parameterises that boundary as a single
> `--test-start` (default `2017-02-01`, the Recruit competition's own split) and
> takes the validation slice as the **tail of the training window** rather than a
> fixed 90/30 split. The discipline is identical: the test window is scored once
> and tunes nothing.

### 6.1 Why random splits are a lie in time series

Suppose a training row is **store A, 2017-03-10**: its `lag_7` is visitors on
**2017-03-03**, and its `roll_mean_7` covers **03-04…03-10**. If the test set
contains **store A, 2017-03-07**, that day is inside the training row's rolling
window — the model was trained on a feature that *contains the value it is later
asked to predict*. Its test MAE looks spectacular and it has learned nothing but
how to read the future. Shuffling is not "roughly fine" here; it invalidates the
entire evaluation.

### 6.2 Why the validation slice is the tail, not a random sample

The tail's only job is to answer "how does this model do **on days right after
its training data**" — exactly the serving situation. A random 10% would be days
*inside* the window, where neighbouring days are in training, and early stopping
would tune for an easier problem. The tail serves four calibration jobs, none of
which may touch test:

| Job | Why it must not use test |
| --- | --- |
| Bounded LightGBM hyperparameter search | Selecting on the test window is fitting to test; the search function is not even given that frame |
| Early stopping (both boosters) | Choosing when to stop on the test window is fitting to test |
| Conformal residual warmup | A conformal guarantee assumes calibration data was not used to tune the model |
| Policy-quantile / risk sweep | These are hyperparameters (Section 3.14 discipline) |

### 6.3 The test window is touched exactly once

This is a discipline, not a technique: **the test window never calibrates,
selects, early-stops or tunes anything.** Every quantity that needed tuning was
chosen on the validation tail.

### 6.4 The rolling-origin backtest split

The backtest (opt-in) anchors folds at the end of the data and walks
**backwards**:

```
fold 1  train ─────────────────┤ test (30d) ┤
fold 2  train ───────────────────────┤ test (30d) ┤
fold 3  train ─────────────────────────────┤ test (30d) ┤
...
```

Each fold advances the origin by `advance_days` (14); folds that would leave less
than `min_train_days` (180) of history are dropped, so short synthetic subsets
produce fewer than six folds. Every fold refits from scratch — no state crosses a
fold boundary — and its test window is scored once.

---

## 7. Training the models

All models train on the **same frame, the same chronological split and the same
`log1p(visitors)` target**; only the estimator differs. This is what makes the
comparisons like-for-like.

### 7.1 Baselines — deliberately building bad models

Before anything sophisticated, the pipeline builds **five stupid models** and
takes them seriously. All fit on the train window only and score the same test
days:

| Baseline | Rule | Why it is here |
| --- | --- | --- |
| `naive` | yesterday's demand (`shift(1)` per store) | The laziest defensible forecast |
| `seasonal_naive` | the same weekday last week (`shift(7)`) | The bar every manager clears in their head |
| `historical_average` | mean of the last 28 comparable days, **frozen** at window start | Approximately what an experienced operator does |
| `weekday_mean` | per-store, per-weekday historical mean | The strongest *simple* signal; usually the winner |
| `global_average` | the training mean | The floor |

`best_baseline` (lowest MAE) is the bar every candidate must clear. The pipeline
re-fits that baseline on the *same feature rows* so the lift is exactly
like-for-like: `mae_lift_vs_baseline = 1 − mae / baseline_mae`.

Each baseline carries an explicit fallback ladder — a store's first in-window day
has no predecessor, so `naive` falls back to the store train mean, then the
global mean. `NaN` propagating into a metric does not produce a *wrong* number,
it produces *every* number wrong at once, and real Recruit data contains stores
that opened mid-window.

**Why MAPE is banned.** Percentage error explodes as the denominator approaches
zero: a day with 1 visitor predicted as 2 is a 100% error and swamps a day with
200 predicted as 210 (5%). Restaurants have plenty of 1-visitor days, so MAPE
would systematically misrepresent exactly the small sites. Worked evidence is in
§7.5.

### 7.2 Fallbacks — is the complexity earned?

Linear Regression, Random Forest and XGBoost train on **exactly the primary
frame** — same rows, features, split and target — so the estimator is the only
variable. This is the evidence behind the claim that the boosted trees earn their
complexity.

Two implementation notes, both forced by the model classes:

* **NaN imputation.** `sklearn` estimators reject NaN; the frame always contains
  some. The fit records the **training-window median per column** and fills with
  it — learned from the fit slice only.
* **Bounded linear output.** An unregularised least-squares fit on a `log1p`
  target is an *exponential* model with no ceiling. One out-of-support row in the
  Recruit test window inverted to **1.8 × 10¹⁰ visitors**, and that single row
  accounted for the model's entire reported MAE. `LinearRegressionModel` clips
  predictions to the target range of the fit slice (in log space) — a property of
  the model class, not a tuned hyperparameter, and learned from the fit slice so
  it carries no leakage.

A missing dependency is reported, not hidden: XGBoost lives in the `training`
extra; if absent it is recorded under `skipped` with the reason and left out of
the ranking — never quietly substituted.

### 7.3 LightGBM — the primary

Chronological early-stopping discipline: fit on the first 90% of the train
window, early-stop on its tail, then score the test window exactly once.

| Parameter | Value | Why this value |
| --- | --- | --- |
| `objective` / `metric` | `regression` / `rmse` (on `log1p`) | Least-squares on the compressed target |
| `learning_rate` | `0.05` | Small corrections converge smoothly; pairs with the 2000-round ceiling |
| `num_boost_round` | `2000` (cap) | Never reached (stopped at 200); a ceiling is what early stopping needs |
| early stopping | `100` rounds | ~5% of the ceiling: rides out noise, still saves time |
| `num_leaves` | `63` | ≈ depth 6, tested capacity for this size; default 31 is a touch small for the interactions present |
| `min_data_in_leaf` | `40` | The anti-memorisation guard — stops 3-row leaves that generalise to nothing |
| `feature_fraction` | `0.9` | Decorrelates near-duplicate demand features without blinding the tree |
| `bagging_fraction` / `bagging_freq` | `0.9` / `1` | Row subsampling for variance reduction; seeded, so deterministic |
| `lambda_l2` | `1.0` | Leaf-value penalty — a second guard behind `min_data_in_leaf` |
| categoricals | `dow`, `month` | Native categorical splits; `month` as a number would imply Dec is far from Jan |
| target transform | `log1p(visitors)` → `expm1`, clipped at 0 | Compress the right skew so a few 800-visitor days do not dominate; matches RMSLE |
| `seed` / `deterministic` / `force_col_wise` | `42` / `True` / `True` | Seeded *and* reproducible — randomness is a modelling device |

**Why leaf-wise growth and leaf count rather than depth.** LightGBM grows
leaf-wise: it splits whichever leaf promises the biggest error reduction,
reaching a given accuracy with fewer splits, but it can produce lopsided trees —
so you constrain the leaf count (63), not the depth. (CatBoost grows symmetric
trees and is specified by depth — part of why the two fail differently.)

**Why introduce randomness at all.** `lag_7`, `roll_mean_7` and `dow_mean_8w` are
all "recent demand" and often nearly interchangeable; a tree given all of them
keeps picking the same one. Restricting each tree's view (90% of features, 90% of
rows) forces diversity. It does not break reproducibility: the seed is fixed and
LightGBM is `deterministic=True`, so the "random" subsets are the same every run.

**Why `log1p` and not raw counts.** Demand is right-skewed (mean ≈ 21, max 877).
On raw counts, one 877-visitor day contributes `(877 − 30)² ≈ 717,000` to the
loss while a typical day contributes ~100 — a handful of huge days would dominate
what the model optimises. `log1p` compresses 0→877 to 0→6.8, so the model learns
the *proportional* structure, which is also what RMSLE measures. The `1 +` makes
zero-demand days legal (`ln(0)` is undefined). Predictions are
`np.clip(expm1(raw), 0, None)`: inverting a log can, in principle, produce a small
negative, and negative visitors are impossible.

**Bounded hyperparameter search (validation tail only).** `stage_tune` evaluates
a fixed eight-configuration grid (`models/search.py:TUNING_GRID`) and selects by
validation MAE, with RMSE then trial order breaking ties. Trial 0 *is* the
production default above, so the reported improvement is against the
configuration the study would otherwise have shipped; a search that finds
nothing better keeps trial 0 and reports a non-positive improvement rather than
inventing a winner. The function is handed the fit and validation frames only —
the test window is not a parameter — and `stage_lightgbm` trains once on the
selected parameters. Every trial is written to `tuning_trials.parquet` and the
selection to `tuning_best.joblib`; `--skip-tune` restores the old behaviour and
leaves `tuning_trial = null` in the LightGBM metrics so the provenance says so.
The backtest and sensitivity stages refit with the same selection, so their
figures describe the production configuration rather than an untuned variant.

### 7.4 CatBoost — the second primary

| Parameter | Value | Why |
| --- | --- | --- |
| `loss_function` | `RMSE` (on `log1p`) | Same target contract as LightGBM |
| `learning_rate` | `0.05` | Same reasoning — consistent so the comparison is fair |
| `depth` | `6` | CatBoost's native capacity knob; ≈ 2⁶ = 64 leaves, matching LightGBM's 63 |
| `l2_leaf_reg` | `3.0` | Its regulariser, counterpart to LightGBM's `lambda_l2` |
| `iterations` / early stopping | `2000` / `100` (`use_best_model=True`) | Same discipline, same patience |

Both are kept **because they fail differently** — LightGBM grows leaf-wise and is
sensitive to `num_leaves`; CatBoost grows symmetric, depth-wise trees with native
categorical handling and is slower but more tuning-robust. If LightGBM
destabilises under a future data change, CatBoost is a genuine alternative, not a
re-run with a different seed.

### 7.5 Metrics: what we measure and what we ban

Defined in `evaluate/metrics.py` (Section 3.15).

| Metric | What it is | Why it is included |
| --- | --- | --- |
| **MAE** | mean absolute error, visitor units | The headline: same units as the decision, robust to the occasional wild day |
| **RMSE** | root mean squared error | Surfaces large misses MAE averages away |
| **RMSLE** | RMSE on `log1p` | The **primary comparison** metric — the distribution is right-skewed, so proportional comparison is fairer across store sizes |
| **MASE** | MAE ÷ seasonal-naive MAE on the training series | Scale-free, and relative to a real benchmark (`m = 7`) |
| **Bias (ME)** | mean of `prediction − actual`, signed | A model right on average but always 2 low is not fine; the sign is the point |
| **R²** | coefficient of determination | Supplementary only, **never** a selection criterion |

**Why R² is reported but never used to choose.** Variance is dominated by the
busiest stores, so a model can raise its R² while getting every small site wrong.
A *negative* R² is a usefully blunt signal that the model is worse than
predicting the mean. **Why MASE's scale comes from the training series:** the
seasonal-naive benchmark must be computed on data the model was allowed to see;
taking it from test would let the test set define its own difficulty.

**Why NaN handling is explicit and loud.** `_pair()` **raises** if the *truth*
contains a NaN (a data bug makes the metric meaningless) and **drops with a
warning** if a *prediction* contains a NaN (real data has stores that opened
mid-window). The asymmetry is deliberate: truth is authoritative, the prediction
is the thing under evaluation.

**Worked evidence — the MAPE trap, on real rows** (grouped by actual demand):

| Actual demand | Days | MAE | Mean % error |
| --- | --- | --- | --- |
| 0–5 | 6,183 | **4.964** | **249.6%** |
| 5–15 | 18,411 | 4.063 | 46.9% |
| 15–30 | 18,623 | 5.718 | 27.4% |
| 30–60 | 12,855 | 9.698 | 24.0% |
| 60+ | 1,794 | **28.513** | 34.9% |

The 0–5 row is the entire argument against MAPE: on absolute terms it is the
*second-best* band (MAE 4.96), but on percentage terms it is catastrophic at
249.6%. Rank this model by MAPE and you conclude it is worst at the easiest rows
— the exact inversion of the truth. The 60+ row is the other honest end: MAE
28.5 against an overall 6.7 — the model is roughly 4× less accurate on the
busiest days in absolute terms, which is why coverage is reported per bucket
(§8) and the 60+ stratum carries a wider band.

**MAE and RMSE disagree about the worst day.** The single worst test row (actual
877, predicted 11.0, error 866) contributes 0.22% of the MAE but **10.07% of all
squared error**. Both are true; they answer different questions, so both are
reported.

The full evaluation protocol — per-slice breakdowns, decision-quality metrics,
post-release monitoring, and the model-card requirements — is
[EVALUATION.md](EVALUATION.md). The Winkler score listed there is part of the
intended protocol and is **not yet computed** by the pipeline; the implemented
metric set is exactly the table above.

---

## 8. Uncertainty: adaptive conformal intervals

### 8.1 Why a range, and why not "± 2 standard deviations"

You need a range because the decision is asymmetric. But assuming normal errors
and taking ±2σ fails here for three reasons: demand errors are **skewed**, the
error scale **varies by store**, and demand is **non-stationary**. **Conformal
prediction** avoids all three by making essentially no distributional assumption:
it produces a range with a guarantee stated in terms of the observable — over the
long run, about 90% of days should land inside it.

### 8.2 The core idea

1. For past days, compute the absolute error `|actual − predicted|`.
2. Take a high quantile `q` (the 90th percentile) — the "half-width".
3. Draw the band as `[prediction − q, prediction + q]`.

The guarantee comes from step 2 being computed on data the model did not train
on. Restaurant demand is not exchangeable (it drifts), so the pipeline uses the
**adaptive** variant — the correction for non-stationarity — and still reports the
**empirical** coverage it actually achieved rather than asserting the guarantee.

### 8.3 Adaptation: what α and γ do

The adaptive method (Gibbs & Candès, 2021) maintains a running miscoverage level
**α** and nudges it after each day:

```
α_{t+1} = clip( α_t + γ · ( target_miscoverage − 1{y_t ∉ interval_t} ) , ε , 1−ε )

where  target_miscoverage = 1 − 0.90 = 0.10     (we want 10% of days to miss)
```

At `γ = 0.02`: a **covered** day moves α by `+0.002` (band narrows slightly); a
**missed** day moves it by `−0.018` (band widens sharply). One miss needs about
nine covered days to undo — it reacts in days, and is stable over weeks. The loop
runs **online**: when scoring day `t`, the interval is built from `α_t` and a
residual quantile that only sees days strictly before `t`.

### 8.4 Stratification (Mondrian), and the failure it fixes

The production intervals are **stratified by *predicted* demand level** (0–15,
15–30, 30–60, 60+): one independent α, one residual window and one warmup slice
per level.

**The failure this fixes.** With a single global quantile, the half-width is
driven by the many quiet days — say ±9 visitors. On a 60+ visitor day the errors
are much larger, so ±9 misses far more often than 10% of the time: the 60+
bucket's coverage **collapsed below target** while overall coverage still *looked*
fine. That is the classic aggregate-hides-the-failure trap.

**Why stratify on the prediction, not the realised value.** At forecast time you
know the prediction and not the truth, so a stratum keyed on the realised value
would be unusable in production — and would be a form of self-leakage. Bucketing
by the prediction keeps the procedure computable with only what is available
before the day starts.

**Why thin buckets fall back to the global band.** A stratum with fewer than
`min_bucket = 30` warmup residuals cannot support a quantile. Rather than emit a
garbage interval, it uses the global ACI's half-width; the global loop runs in
parallel precisely so that fallback is always available. The share of this
fallback is reported (`global_fallback_share`).

**The finite-sample inflation `(1 + 1/n)` and `method="higher"`.** A quantile from
a finite sample underestimates the true quantile on average; the `(1 + 1/n)`
factor is the standard conformal correction, and `"higher"` picks the upper
interpolation point so the band is never narrower than target. The same rule is
used in training, in the backtest and in the exported serving state.

### 8.5 What the intervals looked like (reference run)

```
empirical coverage   89.98 %     (target 90 %)
mean interval width  29.62 visitors
```

By predicted bucket — read the **width** column:

| Bucket | Days | Coverage | Mean width |
| --- | --- | --- | --- |
| 0–15 | 25,958 | 90.0% | **19.4** |
| 15–30 | 20,350 | 90.0% | **33.2** |
| 30–60 | 11,109 | 90.0% | **44.7** |
| 60+ | 449 | 90.6% | **86.0** |

Every bucket lands on 90% while the band for the busiest stratum is **4.4×
wider** than the quiet one. A single global quantile cannot do this: it produces
one width for all four rows and the 60+ coverage collapses. The global-quantile
comparison run is retained as the honesty baseline (coverage 89.95% @ width 31.37,
with realised-60+ coverage of just 51.2%).

Five consecutive days for one store show a band breathe (same store: the *only*
thing that changes is the stratum):

| Date | Prediction | Lower | Upper | Actual | Width | Bucket |
| --- | --- | --- | --- | --- | --- | --- |
| 2017-04-18 | 24.0 | −1.3 | 49.3 | 30 | 50.6 | 15-30 |
| 2017-04-19 | 30.4 | 5.1 | 55.7 | 20 | 50.7 | 30-60 |
| 2017-04-20 | 28.0 | 2.7 | 53.3 | 22 | 50.6 | 15-30 |
| 2017-04-21 | 49.7 | 24.3 | 75.0 | 71 | 50.7 | 30-60 |
| 2017-04-22 | 65.6 | 29.3 | 101.9 | 97 | **72.6** | 60+ |

`lower` can be **negative** (−1.3 on 2017-04-18) and is deliberately **not
clipped**: erring by 1.3 visitors on a quiet day is harmless, while clipping
would break the coverage arithmetic the guarantee rests on. (The *point* forecast
is clipped at zero; the band is not.)

### 8.6 What is reported, and why more than one number

| Reported | Why |
| --- | --- |
| Empirical coverage | Did we hit the ~90% target? |
| Mean interval width | **Coverage alone is gameable** — `[0, ∞)` has perfect coverage |
| Coverage by demand level | Catches the aggregate-hides-the-failure case |
| Coverage by predicted bucket | The same view through the calibration lens |
| Global-quantile comparison | The honesty baseline, so the benefit is measured not asserted |
| Sensitivity at 80% / 95% | Shows the behaviour scales with the target, not one magic number |
| `global_fallback_share` | How much of the estate stratification could not serve |

---

## 9. The decision layer: preparation policy

The model's job ends at `(point, lower, upper)`. Turning that into "prepare N
portions" is a **business** decision, kept deliberately transparent and separate
so it can be argued with and evaluated on its own. Four policies are simulated
over the same held-out window:

| Policy | Preparation quantity | Represents |
| --- | --- | --- |
| `historical_average` | mean of the last 28 comparable days (frozen before the window) | "What an experienced manager does" |
| `point_forecast` | `P = D̂` | "Trust the model exactly" |
| `uncertainty_aware` | `P = upper × scale` (1.0 = prepare to the conformal bound) | "Trust the model's own range" |
| `risk_aware` | `P = D̂ + q`, the newsvendor quantile of past errors | "Price the two mistake types explicitly" |

### 9.1 The newsvendor rule, and what `w` means

If a leftover portion costs 1 unit and leaving a customer unserved costs `w`, the
cost-minimising quantity is the **`w / (1 + w)` quantile of the signed error
distribution** — the newsvendor critical fractile. With `w = 2`, the level is
`2/3 ≈ 0.667`, so you read the 66.7th percentile of `(actual − predicted)` and add
it. Because errors are signed, the correction has a **direction**. The sanity
property that validates the maths: at `w = 1` the level is `0.5` — the **median**
— so the policy degenerates to `D̂ + median error`, a bias-corrected point
forecast. That is not a bug; it is why the sweep is *allowed to conclude* that
padding never pays.

### 9.2 Exposures, and why they are never kilograms

```
SR = max(0, P − D̂)   planned exposure vs the forecast that set the policy
PS = max(0, P − D)    realised over-preparation exposure
UD = max(0, D − P)    unmet-demand exposure
```

All in **visitor-equivalents**, never kilograms. `SR` is zero by construction
under the point-forecast policy, so `PS` is reported beside it — reporting only
`SR` would make one policy look perfect.

### 9.3 Why tuning is on validation, and the honest verdict

The sweep tries scales `0.6 … 1.5` and risk weights `0.5, 1, 2, 3, 5` on the
**validation** window, scoring each on `surplus_ps + ud_weight × unmet_ud`
(`ud_weight = 1.0` — equal costing by default, exposed as a business choice). If
you chose the scale on the test window you would simply be fitting the answer.
The verdict is recorded either way, including the unflattering ones:

* `uncertainty_aware_preferred_at_q<scale>`,
* `risk_aware_preferred_at_w<weight>`,
* `point_forecast_preferred_under_this_costing` — padding never paid.

### 9.4 The reference run's frontier

Whole held-out window (`period_days = 57,866`):

| Policy | Prepared total | `PS` | `UD` |
| --- | --- | --- | --- |
| `historical_average-n28` | 1,153,659.93 | 214,764.75 | 296,135.82 |
| `point_forecast` | 1,138,526.76 | 145,646.89 | 242,151.14 |
| `uncertainty_aware-q0.6` | 1,197,394.74 | 193,481.68 | **231,117.94** |
| `risk_aware-w1` | 1,112,755.79 | **133,855.59** | 256,130.80 |

Headline: `uncertainty_aware-q0.6` vs `point_forecast`: UD down **−4.56%** for
`PS +47,834.79` visitor-equivalents. The sweep selected
`risk_aware_preferred_at_w1` **on validation**; on the test window it wins on
surplus but **loses to the plain point forecast on unmet demand** (256,130.80 vs
242,151.14). The selection was made correctly and the test window still disagreed
with it — that is what honest out-of-sample evaluation looks like, and it is
reported rather than smoothed over.

---

## 10. Explanation: SHAP, and why it is not causation

A prediction is the sum of many small tree contributions. **SHAP** assigns each
feature a share of the difference between this prediction and the average
prediction — the Shapley value from cooperative game theory, computed by
averaging a player's marginal contribution over all orderings. The properties that
matter: contributions add up exactly to `prediction − base` (efficiency),
identical features get identical credit (symmetry), and influence cannot shrink
when a feature's importance grows (consistency). **TreeSHAP** computes these
exactly in polynomial time by walking the tree structure, which is why the
explanation is affordable for every forecast.

Contributions are computed with `shap.TreeExplainer` on the LightGBM booster for
the **same rows it scored**, converted to visitor units by the local
linearisation `shap_raw × exp(raw_pred)`, and stored keyed by `(store, date)` so a
later explanation request **replays** stored values rather than re-deriving them
against a newer model. An explanation that silently changes is worse than none.

### 10.1 The framing constraint

SHAP explains **the model**, not the world. When it says `dow_mean_8w` moved the
prediction by +2.6, it means the model *used* that feature, not that last week's
Wednesdays *caused* this Wednesday's traffic. The data is observational; there was
no experiment. So the pipeline ships a mandatory disclaimer with every
explanation, the framing is always **predictive attribution**, and it is never
presented as causal — not to operators, not in the docs, not in the UI.

### 10.2 The additivity check, and where the gap comes from

The decomposition is **exact in log space** and only approximate after conversion
to visitor units. The model trains in log space while contributions are reported
in visitor units; converting 26 separate log-space impacts with one per-row scale
factor cannot reproduce the curvature of `expm1`. The stage computes
`median_relative_reconstruction_error` and reports it openly — **12.67%** across
the reference window. A single row can easily be worse than the median; the honest
claim is that the contributions decompose the model's *log-space* reasoning
exactly, and the visitor-unit figures are a faithful but linearised rendering.

### 10.3 Worked decomposition

Across all 57,866 forecasts, global importance is led by `dow_mean_8w`
(mean |SHAP| **8.44** visitors), then `roll_mean_7` 2.32, `rsv_visitors` 1.32,
`roll_mean_28` 1.18, `dow` 1.05. A missing value is still a value: `rsv_visitors`
is NaN for stores with no booking feed and still carries a small downward
contribution — only visible because the explanation keeps the row.

---

## 11. Backtesting: why one window is not enough

One held-out window gives you one number — a sample of size one. If that window
happened to be a quiet spring, the number is a statement about *that window* as
much as about the model. The rolling-origin backtest scores six different 30-day
windows and reports a **mean and standard deviation**.

| Parameter | Value | Why |
| --- | --- | --- |
| `n_folds` | 6 | Enough windows to average out a lucky stretch without multiplying cost (each fold refits from scratch) |
| `test_days` | 30 | One month — the natural planning horizon, and several weekly cycles |
| `advance_days` | 14 | A two-week stride: 30 would make folds disjoint and waste data, 1 would make them nearly identical |
| `min_train_days` | 180 | Six months, so no fold lacks a full seasonal shape; shorter folds are **dropped**, not shortened |

**Each fold carries its own split-conformal interval**, built from that fold's own
validation residuals — never the test window — so the coverage figure is an
independent out-of-sample check rather than a restatement of the main run.

Reference run: mean MAE **6.667 ± 0.341** across folds
`6.884 / 7.101 / 6.893 / 6.510 / 6.383 / 6.232`, mean coverage **0.9006**
(per fold 89 / 88 / 88 / 89 / 92 / 95%). Coverage near 90% on every independent
fold is a much stronger claim than 90% on one.

---

## 12. One real run, stage by stage

This is the reference run traced in execution order — `make train-model`
(`--force --with-backtest`, seed 42), a clean rerun. Durations are wall clock on
8 CPUs; total **≈ 5 min 03 s** (`user` time ≈ 4× `real`: LightGBM, CatBoost and
XGBoost all run with `n_jobs=-1`).

The ten recorded stages below sum to **≈ 4 min 44 s** on their own. The
end-to-end total is larger because it also covers interpreter startup and the
serving-bundle export, which measures **≈ 23 s** in isolation (`time python
scripts/export_serving_bundle.py`). This trace predates the `tune` stage, so its
durations are left as recorded; a fresh run inserts `tune` between `features`
and `fallbacks` and records its own timings.

| # | Stage | Duration | What it established |
| --- | --- | --- | --- |
| 1 | `ingest` | 0.82 s | 252,108 visit rows, 829 stores, 92,378 reservation rows, 8 checks passed, source sha256 pinned |
| 2 | `baseline` | 0.50 s | Five baselines on 57,873 test rows; `best_baseline = weekday_mean` (7.667) |
| 3 | `features` | 31.9 s | 251,279 × 26 (`fs-2026.09.2`), leakage gates passed; 829 all-null rows dropped |
| 4 | `fallbacks` | 69.2 s | Linear / RF / XGBoost on the identical 57,866-row frame |
| 5 | `lightgbm` | 5.9 s | Primary model, early-stopped at iteration 200 of 2000 |
| 6 | `catboost` | 12.2 s | Secondary primary, `best_iteration = 868`, `mae_delta_vs_lightgbm = −0.012` |
| 7 | `conformal` | 40.8 s | Adaptive intervals, 89.98% coverage against 90% |
| 8 | `policy` | 6.3 s | Four policies; multiplier tuned on validation → `risk_aware_preferred_at_w1` |
| 9 | `shap` | 80.3 s | 26 contributions stored for all 57,866 forecasts |
| 10 | `backtest` | 35.7 s | 6 rolling folds, mean MAE 6.667 ± 0.341 |
| — | `export` | seconds | `serving_bundle.joblib` written |

Concrete details worth knowing when reading the code:

- **Order is fixed.** `run_pipeline` iterates a canonical `order` list, not the
  CLI's list, so `backtest` always runs last even if flags were appended
  differently.
- **`record_stage` writes after every stage** through an **atomic write**
  (temp file + `os.replace`), so a killed process never leaves a half-written
  `manifest.json`. It records `started_at`, `finished_at`, `duration_s`, `seed`,
  `python_version`, `platform`, library `versions`, `data` and `metrics`.
- **The test predictions are reused, not recomputed.** `conformal` loads
  `lightgbm_predictions.parquet` and asserts it has the same length as the test
  frame, so it cannot silently score a different row set.
- **`catboost` reads `best_iteration = 868`** — a good example of the two boosters
  genuinely behaving differently on the same data.
- **`fallbacks` can appear after `waste_summary` in `manifest.json`** because the
  manifest is keyed by stage name and never reordered. Read `duration_s` /
  `started_at` for the real chronology, not dict order.
- **Only `waste_summary` sits outside `run_pipeline`.** `sensitivity` *is* one
  of the twelve stages (§3) — it is merely opt-in, skipped by the default run's
  `OPT_IN_STAGES` set alongside `backtest`. `waste_summary`, by contrast, is
  never run by `run_pipeline` at all: it is written by the separate
  `make model-waste` command. If a `sensitivity` entry exists in a manifest from
  a run that did not pass `--with-sensitivity`, it is from an **earlier** run —
  check `started_at`.

---

## 13. What a run produces

Everything lands in the checkpoint directory (`artifacts/runs/latest` by default),
driven by `CheckpointStore` (atomic saves + a per-stage manifest).

| Artifact | Produced by | Contents |
| --- | --- | --- |
| `manifest.json` | every stage | seed, data root, timings, metrics, versions per stage |
| `visits.parquet`, `stores.parquet`, `calendar.parquet`, `reservations.parquet` | ingest | normalised inputs |
| `baseline_predictions.parquet`, `baseline_table.parquet`/`.md` | baseline | five-baseline ranking |
| `features.parquet`, `features_meta.joblib` | features | feature frame + column order + version |
| `tuning_trials.parquet`, `tuning_best.joblib` | tune | per-trial validation MAE + selected configuration and the default's score |
| `fallback_table.parquet`/`.md` | fallbacks | linear/RF/XGBoost comparison |
| `lightgbm_model.joblib`, `lightgbm_predictions.parquet`, `validation_predictions.parquet` | lightgbm | fitted booster + predictions + validation tail |
| `catboost_model.joblib`, `catboost_predictions.parquet` | catboost | fitted model + predictions |
| `conformal_predictions.parquet`, `validation_conformal.parquet` | conformal | intervals, bucket labels, validation-tail bounds |
| `policy_results.joblib` | policy | PS/UD/SR per policy + sweep |
| `shap_contributions.parquet`, `shap_meta.joblib` | shap | per-forecast contributions + disclaimer |
| `sensitivity_ablation.parquet` | sensitivity | per-group MAE deltas |
| `backtest_folds.parquet`, `backtest_table.md`, `backtest_summary.joblib` | backtest | per-fold + mean/std metrics |
| `waste_summary.joblib` | `make model-waste` | URBAN-WASTE descriptive stats (never joined to demand) |
| `serving_bundle.joblib` | `scripts/export_serving_bundle.py` | the API's serving artifact |

---

## 14. From a run to the registry

The backend does **not** import the training package. Instead,
`scripts/export_serving_bundle.py` (`make bundle`, included in `make train-model`)
writes a self-contained `serving_bundle.joblib` containing:

* the native LightGBM booster and the exact feature-column order;
* the final online conformal state (per-stratum α + trailing residual windows,
  plus the global fallback), reproduced by **replaying** the same loop;
* the holiday calendar the feature flags assume;
* the run's point-accuracy metrics, best baseline, evaluation windows and booster
  hyperparameters;
* the backtest folds and summary, so per-fold coverage reaches the console;
* the fallback comparison table.

One subtlety in the export: the online loop's final α is a drifting snapshot that
**under-covers** if used statically. The export re-calibrates a serving α per
stratum by searching from wide to narrow and returning the **narrowest** α whose
quantile still reaches 90% on the residuals the loop consumed, so the served
interval matches the measured coverage.

The API loads the bundle through `app/services/forecasting/loader.py` and
materialises/updates `model_versions` for the workspace — so the Model Registry,
Accuracy and Experiments screens show the numbers the run actually measured
rather than nulls. Because the pipeline is deterministic, re-running it registers
the same versions with the same metrics (idempotent).

> `POST /api/v1/models/train` returns **501 `training_unavailable`**. Training is
> `make train-model`; the console deliberately cannot queue a run.

---

## 15. Reproducibility and the audit receipt

### 15.1 Determinism

Every entry point calls `set_global_seed(seed)` once before any model code runs:
`random.seed`, `np.random.seed`, `PYTHONHASHSEED`. With LightGBM `deterministic=True`
and a fixed `seed`, re-running the pipeline reproduces the same metrics and the
same model versions. The serving bundle's file hash changes between runs only
because it embeds a `created_at` timestamp — strip that one field and two exports
of the same run are identical. This is stated explicitly because an auditor who
hashes the bundle and sees it change would reasonably suspect non-determinism.

### 15.2 The verification receipt

A verification re-run was executed on **2026-10-01** with byte-identical inputs
and the identical command: snapshot the manifest, rerun `make train-model`, diff.

| Check | Result |
| --- | --- |
| Exit code | **0** |
| Scalar metrics compared across the two manifests | **100** |
| Differences found | **0** |
| `source_sha256` map (input data) | **identical** |
| `lightgbm_model.joblib` md5 | **`75b63a51996ecb0043c134a249e0f1b9`** — identical before/after |
| Bundle content (all fields except `created_at`) | **identical** |

Read that as the central claim: the second run produced a byte-identical model
artifact and identically-valued output on all 100 recorded metrics.

The reference environment, recorded per stage:

```
python      3.11.15     platform macOS-15.8-x86_64-i386-64bit     cpu_count 8
versions    pandas 3.0.6 · numpy 2.3.5 · scikit-learn 1.9.1 · lightgbm 4.7.0
            catboost 1.2.10 · xgboost 3.2.0 · joblib 1.6.0
```

**Honest gap, stated rather than glossed.** `shap` and `pyarrow` are installed and
used but are **not** in the manifest's `versions` block (it covers seven packages,
not nine). `pyproject.toml` uses ranged requirements and there is no lockfile, so
the recorded versions are the *receipt for this run* rather than a pinned
reconstruction. A stricter audit would add both to the version block and commit a
lockfile. See §17.

### 15.3 Data provenance

Pinned by content hash at ingest (recorded in the manifest and carried into the
serving bundle's `provenance` block):

| File | sha256 (prefix) |
| --- | --- |
| `air_visit_data.csv` | `6bbf58c94337…` |
| `air_store_info.csv` | `355af01df53b…` |
| `date_info.csv` | `67fe2fdec775…` |
| `air_reserve.csv` | `50c6ce5583e8…` |

**Why content hashes rather than a git commit.** They anchor provenance on the
*bytes actually consumed*, which survives re-downloads, renames and branch
changes. The hash map compared identical across the two runs above.

### 15.4 Verify it yourself

Run from the repository root; each check is self-contained and does not depend on
this document being correct.

```bash
# 1. Re-run training exactly as recorded (~5 minutes)
make train-model && echo "exit=$?"          # expect 0

# 2. Prove determinism — two runs, diff the manifests (ignoring timestamps)
cd model
.venv/bin/python -m diq_model.train train --data-root data/recruit \
  --checkpoints /tmp/diq-run-a --test-start 2017-02-01 --seed 42 --with-backtest --force
.venv/bin/python -m diq_model.train train --data-root data/recruit \
  --checkpoints /tmp/diq-run-b --test-start 2017-02-01 --seed 42 --with-backtest --force
diff <(python -c "import json;print(json.dumps(json.load(open('/tmp/diq-run-a/manifest.json')),sort_keys=True,indent=1))" \
        | grep -v -e started_at -e finished_at -e duration_s) \
     <(python -c "import json;print(json.dumps(json.load(open('/tmp/diq-run-b/manifest.json')),sort_keys=True,indent=1))" \
        | grep -v -e started_at -e finished_at -e duration_s)
# expect: no output

# 3. Confirm the input data matches what was audited
cd model && for f in air_visit_data air_store_info date_info air_reserve; do
  shasum -a 256 "data/recruit/$f.csv"
done

# 4. Confirm the recorded hyperparameters are the ones used
cd model && .venv/bin/python -c \
  "import joblib;print(joblib.load('artifacts/runs/latest/serving_bundle.joblib')['metrics']['hyperparameters'])"

# 5. Recompute the headline metrics from the raw predictions
cd model && .venv/bin/python -c "
import pandas as pd, numpy as np
p=pd.read_parquet('artifacts/runs/latest/lightgbm_predictions.parquet')
y=p.visitors.to_numpy(float); q=p.prediction.to_numpy(float)
print('MAE ', round(np.abs(y-q).mean(),3))
print('RMSE', round(np.sqrt(((y-q)**2).mean()),3))
print('RMSLE', round(np.sqrt(((np.log1p(q)-np.log1p(y))**2).mean()),4))
"                                            # expect: 6.702 / 11.346 / 0.4934

# 6. The leakage gate the whole evaluation depends on
cd model && .venv/bin/python -m pytest tests/test_leakage.py -q

# 7. The full model test suite
cd model && .venv/bin/python -m pytest -q

# 8. The API cannot train; the container cannot write the model
curl -s -o /dev/null -w '%{http_code}\n' -X POST localhost:8000/api/v1/models/train  # 501
grep -n './model:/model' docker-compose.yml                                          # ./model:/model:ro
```

### 15.5 Measured reference run

| Figure | Value |
| --- | --- |
| Rows / stores ingested | 252,108 visit rows; 829 stores |
| Feature frame | 251,279 rows × 26 (`fs-2026.09.2`) — 193,413 train / 57,866 test |
| Best baseline MAE | 7.667 (`weekday_mean`) |
| **LightGBM test MAE / RMSE / RMSLE** | **6.702 / 11.346 / 0.4934** (MASE 0.634, Bias −1.668, R² 0.582) |
| LightGBM `best_iteration` | 200 of 2000 |
| CatBoost MAE | 6.690 (`mae_delta_vs_lightgbm = −0.012`) |
| XGBoost / Random Forest / Linear Regression MAE | 6.684 / 6.764 / 8.921 |
| LightGBM lift over best baseline | **12.6%** (`0.1262`) |
| Conformal test coverage / width | 89.98% @ 29.62 visitors |
| Backtest mean MAE (6 folds) | 6.667 ± 0.341; coverage 0.9006 |
| SHAP base value / top feature | 15.562 visitors; `dow_mean_8w` (8.44) |

---

## 16. Governance and promotion gates

### 16.1 Who can change what

| Control | Mechanism | Where it is enforced |
| --- | --- | --- |
| Training cannot be triggered from the product | `POST /api/v1/models/train` → `501 training_unavailable` | `backend/app/routers/models.py` |
| Serving cannot write model artifacts | `./model:/model:ro` read-only mount | `docker-compose.yml` |
| No model is selected automatically | Comparison figures are *reported*; `status` stays `candidate` | `export_serving_bundle.py`, registry |
| Promotion needs a second human | Change-request queue, administrator approval | `change_requests` + admin UI |
| Rollback is a pointer change | Every version keeps its artifact, window and feature-set version | registry / bundle |
| Overfitting to the test window is structurally prevented | Test window used once; all tuning on the validation tail | `train.py` stage discipline |
| The report cannot go stale | `make chapter4-check` regenerates and diffs the committed draft | CI + `model/Makefile` |

### 16.2 The promotion gates

From [EVALUATION.md](EVALUATION.md), all five of:

1. MAE improves over production by **≥ 3%** on the rolling-origin mean.
2. **No slice** degrades by more than **15%** — including the top and bottom
   demand deciles.
3. Empirical coverage lands within **88–92%**.
4. Inference stays under **300 ms** per location-day at p95.
5. A **model card** exists naming the training window, feature-set version,
   hyperparameters, metrics by slice, and known weaknesses.

Current status: the pipeline produces the measurements these gates are evaluated
against. The promotion decision is a separate, human step; nothing in this
document should be read as that decision having been made.

Post-release monitoring — rolling MAE drift, coverage breaches, feature PSI, and
fallback rate, each with its response — is defined in
[EVALUATION.md](EVALUATION.md). Recalibration is always tried before retraining,
because most coverage drift is an interval problem, not a point-forecast one.

---

## 17. Known limitations

Collected in one place so nothing is discovered under questioning. Each is a real
gap, not a hedge.

| # | Limitation | Impact | Mitigation |
| --- | --- | --- | --- |
| 1 | `shap` and `pyarrow` versions are not recorded in the manifest | Version receipt incomplete by two packages | Add both to the `versions` block |
| 2 | Requirements are ranged (`>=`), no lockfile | Byte-level environment reconstruction not guaranteed across machines | Commit a lockfile, or record `pip freeze` per run |
| 3 | Bundle file hash changes per export (embedded `created_at`) | Naive hashing suggests non-determinism | Hash with `created_at` stripped, or move the timestamp beside the artifact |
| 4 | `manifest.json` can retain earlier-vintage stages (`sensitivity` from a prior run) | Mixed-provenance manifest | Filter by `started_at`, or run `--with-sensitivity` in the reference command |
| 5 | `waste_summary` records `seed: 0` (all other stages: 42) | Cosmetic inconsistency in a non-training stage | Set the seed consistently |
| 6 | `bias = −1.668` — the model under-predicts by ~1.7 visitors/day | Systematic, directional error | Exposed as a business choice; the `risk_aware` policy corrects for it by construction |
| 7 | MAE is ~4× worse on the 60+ demand decile (28.5 vs 6.7) | Weakest on the days that matter most operationally | Reported per slice; the 60+ stratum carries a 4.4×-wider interval |
| 8 | `risk_aware-w1` was selected on validation but **lost to the point forecast on UD** on test | The selected policy did not win out-of-sample | Reported rather than smoothed over |
| 9 | The repository has no git commits | No code revision can be cited | Provenance anchored on data content hashes |
| 10 | `fs-2026.09.3` (Golden Week features) was evaluated and **not** promoted | A rejected feature set | Documented with its numbers in EVALUATION.md; the test window contains no Golden Week days |
| 11 | URBAN-WASTE is characterised statistically, never joined | No row-level waste-labelling | Deliberate boundary: research data under its own access conditions |
| 12 | Tail behaviour: days whose realised demand outruns the point forecast inherit a quiet bucket's bounds | Protection on the very busiest days remains partial | A tail-prediction problem, deferred to future work on peak-day features |
| 13 | The dataset ends April 2017 in one market | Transportability untested | The pipeline is reproducible end-to-end; other markets/regimes are future work |

---

## 18. Parameter cheat-sheet

Every knob in one table: what it does, why it has that value.

| Parameter | Value | Does | Why this value |
| --- | --- | --- | --- |
| `seed` | 42 | Seeds python/numpy/hash | One fixed seed everywhere (Section 3.27) |
| `test_start` | `2017-02-01` | The one chronological boundary | The Recruit competition's own split; test is never tuned on |
| Validation slice | last 10% of train | Early stopping, conformal warmup, policy sweep | Must resemble "days right after training", not a random sample |
| `FEATURE_SET_VERSION` | `fs-2026.09.2` | Pins feature definitions | A mismatch raises a hard error |
| `learning_rate` | 0.05 | Share of each tree's correction accepted | Small corrections converge smoothly; pairs with the 2000-round ceiling |
| `num_boost_round` | 2000 | Ceiling on trees | Never reached (stopped at 200); a ceiling is what early stopping needs |
| Early stopping | 100 rounds | Patience before stopping | ~5% of the ceiling: rides out noise, still saves time |
| `num_leaves` | 63 | Per-tree capacity | ≈ depth 6; default 31 is a touch small for the interactions present |
| `min_data_in_leaf` | 40 | Minimum rows per leaf | The anti-memorisation guard |
| `feature_fraction` | 0.9 | Features per tree | Decorrelates near-duplicate demand features without blinding the tree |
| `bagging_fraction` / `bagging_freq` | 0.9 / 1 | Rows per round | Variance reduction; seeded, so still deterministic |
| `lambda_l2` | 1.0 | Leaf-value penalty | Second guard behind `min_data_in_leaf` |
| `dow`, `month` | categorical | Native categorical splits | `month` as a number would imply Dec is far from Jan |
| Target transform | `log1p` → `expm1` | Compress the right skew | Stops a few 800-visitor days dominating; matches RMSLE |
| CatBoost `depth` / `l2_leaf_reg` | 6 / 3.0 | Native capacity / regulariser | ≈ 64 leaves, matching LightGBM; counterpart to `lambda_l2` |
| Conformal target | 0.90 | Nominal coverage | A business statement: caught out ~1 day in 10 |
| **γ (gamma)** | **0.02** | **Interval adaptation speed** | **One miss moves α −0.018, needing ~9 covered days to undo — reacts in days, stable over weeks** |
| Conformal window | 200 | Trailing residuals for the quantile | Enough for a stable estimate, short enough to describe "now" |
| `min_history` | 20 | Residuals before an interval exists | Below this the quantile is meaningless; those days are excluded and the count reported |
| `min_bucket` | 30 | Warmup residuals for a stratum | Thin strata fall back to the global band instead of emitting garbage |
| Demand bins | 0-15 / 15-30 / 30-60 / 60+ | Calibration strata | Contain the 60+ tail collapse a single global quantile causes |
| Quantile method | `"higher"` + `(1+1/n)` | Conformal quantile rule | Never narrower than target; corrects finite-sample bias; used identically in training, backtest and serving |
| `ud_weight` | 1.0 | Cost of unmet vs surplus | Equal costing by default, exposed as a business choice |
| `DEFAULT_RISK_WEIGHT` | 2.0 | Stock-out cost ratio | Level 2/3; at w=1 it correctly degenerates to the median (bias correction) |
| Risk sweep | 0.5, 1, 2, 3, 5 | Cost ratios tried | Spans "cheaper than a leftover" to "much more" |
| Uncertainty sweep | 0.6 … 1.5 | Multiplier on the conformal upper bound | Tuned on validation; includes <1 so padding must justify itself |
| Backtest | 6 / 30 / 14 / 180 | Folds / test days / stride / min train | Six ~monthly windows spanning ~3 months, each with ≥6 months of history |

---

## 19. Glossary

| Term | Meaning in this project |
| --- | --- |
| **α (alpha)** | Conformal *miscoverage level*. Starts at 0.10 (= 1 − 0.90); larger α means a narrower band |
| **γ (gamma)** | Conformal *adaptation rate* (0.02). How far α moves per day |
| **Baseline** | A deliberately simple reference model a real model must beat |
| **Boosting round** | Adding one more tree to the ensemble. **Not** an epoch |
| **Coverage** | Fraction of days whose actual demand fell inside the predicted range |
| **Epoch** | One full pass over the training set. **Does not exist in this pipeline** (a neural-network concept) |
| **Feature set version** | `fs-2026.09.2` — pins the feature definitions; a mismatch is a hard error |
| **Fit phase** | Building the 26-feature frame from raw demand (exploratory; the name differs from model "training") |
| **Fold** | One rolling-origin split: a train window and the test window after it |
| **Leakage** | A feature or split that lets a model see the answer. Caught structurally, not by convention |
| **Learning rate** | Share of each tree's correction accepted per round (0.05) |
| **MASE** | MAE scaled by the seasonal-naive error — scale-free across store sizes |
| **Mondrian / stratified conformal** | One conformal calibrator per predicted demand level |
| **Newsvendor quantile** | The `w/(1+w)` error quantile that minimises expected cost for cost ratio `w` |
| **PS / UD / SR** | Realised over-preparation surplus / unmet demand / planned surplus, in visitor-equivalents |
| **RMSLE** | RMSE computed on `log1p` — the primary comparison metric here |
| **Serving bundle** | Self-contained artifact the API loads; contains no training code |
| **SHAP** | Shapley-value attribution of a prediction to its features. **Attribution, not causation** |
| **Split conformal** | Calibrating a band from a held-out residual set (used per backtest fold) |
| **Stratum / bucket** | A predicted demand level (0–15, 15–30, 30–60, 60+) |
| **Warmup** | The train-window residuals that prime the conformal loop before it reaches the test window |
