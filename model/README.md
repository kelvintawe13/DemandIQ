# DemandIQ ML Pipeline

Forecasting, uncertainty and explainability components for demand prediction.
New here? **[PIPELINE.md](PIPELINE.md)** is the single source of truth: the data
and why it is never "cleaned", the leakage-safe features, the chronological split,
every training stage with its exact hyperparameters, the uncertainty and decision
layers, and how to reproduce and audit a run — with worked numbers throughout.

## Status

**Full methodology implemented** (`diq_model` package): Recruit loaders, the
Section 3.7.1 audit, leakage-safe features (3.10) with structural leakage
probes, MAE/RMSE/RMSLE + MASE/Bias (3.15; MAPE deliberately excluded), the
**full five-baseline gauntlet** (3.12 / EVALUATION.md: naive, seasonal
naive, historical average, weekday mean, global average), **LightGBM +
CatBoost** primaries (3.13-3.14) with a **bounded hyperparameter search
scored only on the validation tail** (3.14/3.25; trials are persisted and
published as experiments), the **Section 3.13 fallbacks** (Linear
Regression, Random Forest, XGBoost, trained on the primary feature frame and
scored on the same held-out split), **adaptive conformal intervals** (3.16),
the **preparation-policy simulation** (3.17-3.20), **per-forecast SHAP**
with the mandatory predictive-attribution framing (3.24), and the
**rolling-origin backtest** (3.25; opt-in via `--with-backtest`).

**Training runs on Google Colab** - browser notebook ([COLAB.md](COLAB.md))
or fully headless via the **official Colab CLI** (`make colab-train`). The
package itself is platform-agnostic; the same code runs locally, in Docker,
on Colab, or on Kaggle kernels. Only paths differ.

## Tech Stack

- **Language**: Python 3.11 (matches the Colab runtime)
- **Data**: pandas, NumPy, Parquet checkpoints (pyarrow)
- **ML Frameworks** (training extra): LightGBM, CatBoost, XGBoost
- **Explainability**: SHAP

## Installation

```bash
cd model
uv venv --python 3.11 .venv
uv pip install -e ".[dev]" pyarrow          # dev + tests
uv pip install -e ".[training]"             # adds LGBM/CatBoost/XGB/SHAP
```

## Development

```bash
.venv/bin/python -m pytest          # full suite, synthetic data, no download needed
black src tests && ruff check src tests
```

## Project Structure

```
model/
├── src/diq_model/
│   ├── constants.py        # Audited dataset figures (Section 3.4.1)
│   ├── repro.py            # Global seeding (Section 3.27)
│   ├── artifacts.py        # CheckpointStore: atomic saves + stage manifests
│   ├── data/
│   │   ├── load.py         # Recruit CSV loaders (Section 3.4.1)
│   │   └── audit.py        # Section 3.7.1 audit + per-store coverage
│   ├── features/
│   │   └── build.py        # Leakage-safe features (3.10) + gates + versioning
│   ├── models/
│   │   ├── baselines.py    # All five EVALUATION.md baselines (3.12)
│   │   ├── lightgbm_model.py  # Primary model: log1p, early stopping (3.13)
│   │   ├── search.py          # Bounded validation-tail hyperparameter search (3.14)
│   │   ├── catboost_model.py  # Secondary primary, fallback (3.13)
│   │   └── fallback_models.py # Linear / RF / XGBoost comparison models (3.13)
│   ├── uncertainty/
│   │   └── conformal.py    # Adaptive conformal intervals (3.16)
│   ├── policy/
│   │   └── simulate.py     # SR / PS / UD exposures (3.17-3.20)
│   ├── explain/
│   │   └── shap_values.py  # Per-forecast TreeSHAP (3.24)
│   ├── evaluate/
│   │   ├── metrics.py      # MAE, RMSE, RMSLE, MASE, Bias, R² (3.15)
│   │   ├── backtest.py     # Rolling-origin backtest (3.25, opt-in)
│   │   └── tables.py       # Chapter-4 result tables
│   └── train.py            # CLI: 10 resumable stages (12 total; sensitivity + backtest opt-in)
├── tests/                  # tests incl. leakage, search, fallback + update-rule suites
├── notebooks/
│   ├── DemandIQ_03_Training_and_Export_final.ipynb  # THE notebook: train, evaluate, export
│   ├── DemandIQ-03-Training-and-Export.ipynb        # superseded draft of the same flow
│   ├── DemandIQ-01-Data-and-Baseline.ipynb          # reference: data/architecture figures
│   └── DemandIQ-02-Results.ipynb                    # reference: Chapter 4 tables and figures
├── scripts/
│   ├── render_chapter4.py  # Generates CHAPTER4_DRAFT.md + figures/ from the artifacts
│   ├── check_chapter4_fresh.py  # Fails if committed draft/figures drifted (make chapter4-check)
│   ├── analyze_waste.py    # URBAN-WASTE descriptive analysis (3.21) → waste_summary.joblib
│   ├── make_synth.py       # Synthetic Recruit-shaped data for smoke runs
│   ├── colab/              # Official Colab CLI: train_remote.py + config gen
│   └── kaggle/             # Kaggle kernel push: run_train.py + metadata
├── CHAPTER4_DRAFT.md       # Generated: make chapter4 (never edit by hand)
├── figures/                # Generated report figures (committed alongside the draft)
├── Makefile                # make smoke / train / colab-train / kaggle-push / chapter4-check
├── COLAB.md                # Colab runbook (notebook AND headless CLI paths)
├── COLAB-KAGGLE.md         # Kaggle-kernel training runbook
├── data/                   # Raw data cache (gitignored)
├── artifacts/              # Local checkpoints (gitignored)
├── FEATURE_SPEC.md         # Feature definitions + leakage rules
├── MODEL_LIFECYCLE.md      # Training through promotion
├── PIPELINE.md             # Source of truth: data → split → training, end to end
├── EVALUATION.md           # Metrics and promotion gates
└── AGENT_DESIGN.md         # Assistant grounding rules
```

## The leakage boundary

The feature build enforces the information boundary **structurally**, not by
convention (FEATURE_SPEC.md, proposal Section 3.26):

- rolling windows are computed on the `shift(1)`-ed series;
- `assert_no_window_overlap` re-runs the build with each probe row's target
  value replaced by poison and fails loudly if any historical feature moved;
- the test suite (`tests/test_leakage.py`) proves history flows strictly
  forward: poisoning day T may only change features on days **after** T;
- every frame carries `fs-2026.09.2`; a model asked to predict on a
  mismatched version raises a hard error.

## The baseline gauntlet

The baseline stage (Section 3.12, EVALUATION.md "Baselines") evaluates ALL
of these on the same chronological window, with MASE and Bias:

| Baseline | Definition |
| --- | --- |
| `naive` | yesterday's demand |
| `seasonal_naive` | the same weekday last week |
| `historical_average` | mean of the last 28 comparable days (frozen) |
| `weekday_mean` | mean of the same weekday over 8 weeks |
| `global_average` | the floor |

`best_baseline` (lowest MAE) is the bar a candidate model must clear; the
LightGBM/CatBoost stages report their lift against exactly that bar.
MAPE is deliberately not implemented: it explodes on low-demand days and
would misrepresent small sites (EVALUATION.md).

## The Chapter 4 report and its figures

`CHAPTER4_DRAFT.md` and everything under `figures/` are **generated, never
hand-edited**: `scripts/render_chapter4.py` reads a run's manifest, stage
parquets and joblib artifacts and renders every table and figure from them -
the same "every number traces to a stage" rule the pipeline enforces (Section
3.27).

```bash
make chapter4          # render CHAPTER4_DRAFT.md + figures/ from $(CKPT_DIR)
make chapter4-check    # fail if the committed draft/figures drifted from the artifacts
```

`make chapter4` emits four figures next to the tables and embeds them in the
draft:

| Figure | Shows |
| --- | --- |
| `figures/fig_target.png` | the target distribution (raw vs `log1p`) and mean demand by weekday |
| `figures/fig_corr.png` | Spearman correlations among the 26 leakage-safe features |
| `figures/fig_missing.png` | structural missingness (never imputed) |
| `figures/fig_architecture.png` | the LightGBM architecture + gain importance |

Every value plotted comes from `features.parquet` and the saved booster, so the
figures cannot disagree with the trained model. The renderer skips the figure
section gracefully if `features.parquet` is absent.

Because the draft is generated, changing `render_chapter4.py` without
re-rendering leaves the committed report stale. `make chapter4-check` catches
exactly that: it regenerates the draft into a temp directory (never touching the
working tree), diffs it character-for-character against the committed copy, and
verifies every figure the draft references exists and is non-empty. It **skips
with exit 0** when `artifacts/` is missing - a fresh checkout, since
`artifacts/` is gitignored - so it never fails a build that could not have
produced a draft; pass `--strict-figures` to also byte-compare the PNGs (off by
default, as PNG bytes differ across matplotlib builds). CI runs it via the
`Chapter 4 freshness` step in the `model` job.

**The canonical notebook is
[`notebooks/DemandIQ_03_Training_and_Export_final.ipynb`](notebooks/DemandIQ_03_Training_and_Export_final.ipynb).**
It runs every stage, reports from the run's own artifacts, and exports the
`serving_bundle.joblib` the backend loads. `DemandIQ-01` and `DemandIQ-02` remain
as references for the data/architecture figures and the Chapter 4 tables;
`DemandIQ-03` is a superseded draft. See [`notebooks/README.md`](notebooks/README.md).

## CLI

```bash
# Section 3.7.1 audit only (exit 1 on failure)
.venv/bin/python -m diq_model.train audit --data-root data/recruit

# Full pipeline: ingest, baseline, features, fallbacks (linear/RF/XGBoost),
# lightgbm, catboost, conformal, policy, shap (all checkpointed)
.venv/bin/python -m diq_model.train train \
  --data-root data/recruit --checkpoints artifacts/runs/latest \
  --test-start 2017-02-01

# Stop after features (e.g. to inspect the feature frame first)
.venv/bin/python -m diq_model.train train --data-root ... --skip-lightgbm

# Subset/smoke run: blocks only on hard integrity failures
.venv/bin/python -m diq_model.train train --data-root ... --no-strict

# Everything + rolling-origin backtest (Section 3.25)
make train-full
```

## Remote / CLI training (no local GPU, no browser)

| Command | What it does |
| --- | --- |
| `make smoke` | Full pipeline on synthetic data - no download, no credentials |
| `make train` | Real data locally (DATA_ROOT, default ./data/recruit) |
| `make train-full` | Local + rolling-origin backtest |
| `make colab-train` | Runs the pipeline on a Colab VM via the official Colab CLI |
| `make kaggle-push` | Pushes training to a free Kaggle kernel via the Kaggle CLI |

All remote paths execute the same `run_pipeline` against a checkpoint store,
so artifacts are identical given the same seed, data, and code ref. Setup and
credentials: [COLAB.md](COLAB.md) (Paths A-D) and [COLAB-KAGGLE.md](COLAB-KAGGLE.md).

## Key Contracts

```python
predict(location_id: str, target_date: date, as_of: datetime) -> Forecast
    # -> point, lower, upper, coverage_target, model_version_id, feature_set_version

explain(forecast_id: str) -> list[Contribution]
    # -> feature, value, impact (signed, visitor units)

simulate(location_id: str, period: tuple[date, date], policies: list[Policy])
    -> list[PolicyResult]
    # -> prepared_total, surplus_sr, surplus_ps, unmet_ud, surplus_days, unmet_days
```

## Non-Negotiable Rules

1. **No feature may read the target day** - Enforced by test, not convention
2. **Every forecast carries an interval and model version** - No bare numbers
3. **Surplus is simulated exposure in visitor-equivalents** - Not measured waste
4. **Reproducibility** - Fixed seeds, checkpoint manifests, pinned versions
