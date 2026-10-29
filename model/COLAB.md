# Training on Google Colab

The `diq_model` package is platform-agnostic; this runbook is the Colab
driver. The design principle: **code in git, data in Drive, checkpoints in
Drive, nothing precious in the ephemeral VM.**

## Why this architecture

| Concern | Solution |
| --- | --- |
| No local GPU needed | Training runs on Colab (free T4 / CPU; Recruit is small - LightGBM trains in 1-3 min on CPU alone) |
| Runtime resets | Every stage writes a checkpoint to Drive before the next starts; re-running resumes |
| Reproducibility (S3.27) | Manifest per stage: seed, timestamps, durations, platform and library versions. The manifest records no code commit (the repository has no commits); the remote wrappers pin the ref they clone, and input data is pinned by sha256 |
| Laptop stays clean | Raw Kaggle data cached in Drive; local machine only needs the package for dev/tests |

## One-time setup (5 minutes)

1. **Kaggle token**: kaggle.com > Account > Create New API Token -> saves
   `kaggle.json`. Upload it to Drive at `MyDrive/kaggle/kaggle.json`.
2. **Repo access**: the notebook clones your repo. For a private repo, add a
   GitHub token or make the repo public.
3. Open `model/notebooks/DemandIQ_03_Training_and_Export_final.ipynb` in Colab
   (GitHub URL works: File > Open notebook > GitHub). This is the canonical
   notebook; the others are retained as development history (see
   [`notebooks/README.md`](notebooks/README.md)).

## Run

Edit the **CONFIG** cell (`REPO_ROOT`, `DATA_ROOT`, `CKPT_DIR`, flags) if
needed, then **Runtime > Run all**.

The **DATA** cell verifies the four Recruit CSVs already committed under
`model/data/recruit`, and only contacts Kaggle when one is missing. It is
idempotent: if the CSVs are already there, it does nothing.

The **TRAIN** cell runs the pipeline:

```
[run ] ingest ...
Recruit dataset audit
------------------------------------------------------------
[PASS] duplicate_store_days: 0 duplicate (store, date) pairs
...
[done] ingest in 0.8s -> {'visit_rows': 252108, 'stores': 829, 'audit_checks': 8, ...}
[run ] baseline ...
[done] baseline in 0.5s -> {'best_baseline': 'weekday_mean', 'best_baseline_mae': 7.667, ...}
[run ] features ...
[done] features in 31.9s -> {'rows': 251279, 'features': 26, 'feature_set_version': 'fs-2026.09.2'}
[run ] fallbacks ...
[done] fallbacks in 69.2s -> {'best_fallback': 'xgboost', 'best_fallback_mae': 6.684, ...}
[run ] lightgbm ...
[done] lightgbm in 5.9s -> {'mae': 6.702, 'rmsle': 0.4934, 'best_iteration': 200, 'mae_lift_vs_baseline': 0.1262}
[run ] catboost ...
[done] catboost in 12.2s -> {'mae': 6.690, 'mae_delta_vs_lightgbm': -0.012}
[run ] conformal ...
[done] conformal in 40.8s -> {'empirical_coverage': 0.8998, 'mean_interval_width': 29.62, ...}
[run ] policy ...
[done] policy in 6.3s -> {'uncertainty_scale': 0.6, 'headline': {'ud_reduction_vs_point': 0.0456, ...}}
[run ] shap ...
[done] shap in 80.3s -> {'rows_explained': 57866, 'base_value_visitors': 15.562, ...}
```

The block above is the pre-tuning reference run. A current default run inserts
`tune` between `features` and `fallbacks`: a bounded eight-configuration
LightGBM search scored only on the final 10% of the training window, which
selects the parameters `lightgbm` then trains with. It adds ten stages in
total; `sensitivity` and `backtest` remain opt-in (`--with-sensitivity` /
`--with-backtest`). Timings are from the local reference run — on Colab they
differ, but the stage list and metric keys are the same. See
[PIPELINE.md](PIPELINE.md) for every value.

## Resume after a crash

Just **Run all** again. Output:

```
Completed stages so far: ['baseline', 'ingest']
[skip] ingest (checkpointed) -> {...}
[skip] baseline (checkpointed) -> {...}
```

Add `force=True` to `run_pipeline(...)` to recompute everything.

## Artifact layout in Drive

```
MyDrive/demandiq/
  data/recruit/                  # air_visit_data.csv, air_store_info.csv,
                                 # date_info.csv, air_reserve.csv
  artifacts/colab-run/
    manifest.json                # stage index: metrics, timings, versions, seed
    visits.parquet               # ingest stage outputs
    stores.parquet
    calendar.parquet
    reservations.parquet
    baseline_predictions.parquet # baseline stage output
    baseline_table.parquet       # five-baseline ranking + .md copy
    features.parquet             # leakage-safe feature frame
    features_meta.joblib         # feature-set version + column list
    tuning_trials.parquet        # per-trial validation MAE from the bounded search
    tuning_best.joblib           # selected config + the default's validation MAE
    fallback_table.parquet       # linear / RF / XGBoost comparison + .md copy
    validation_predictions.parquet # LightGBM validation-tail predictions
    lightgbm_model.joblib        # trained booster + provenance
    lightgbm_predictions.parquet # held-out window predictions
    catboost_model.joblib        # secondary primary + provenance
    catboost_predictions.parquet
    conformal_predictions.parquet # lower/upper bounds per test day
    validation_conformal.parquet # validation-tail bounds (policy tuning input)
    policy_results.joblib        # SR/PS/UD trade-off table (all policies)
    shap_contributions.parquet   # per-forecast TreeSHAP (long format)
    shap_meta.joblib             # disclaimer + feature-set version
    backtest_folds.parquet       # rolling-origin folds (+ table.md, summary.joblib)
    serving_bundle.joblib        # written by export, not by run_pipeline
```

The default run is the ten stages above (including `tune`); pass `stages=[...]`
to `run_pipeline` to restrict it, or `--with-sensitivity` / `--with-backtest`
to add the opt-in stages. `--skip-tune` trains LightGBM with the documented
defaults instead of the search's selection.

`manifest.json` is the provenance record the `model_versions` table expects
(MODEL_LIFECYCLE.md): any number in a report can be traced to a stage,
a seed, a library set and a data checksum.

## Running the same pipeline locally

Identical code; only paths differ:

```bash
cd model
uv venv --python 3.11 .venv
uv pip install -e ".[dev,training]" pyarrow
.venv/bin/python -m diq_model.train train \
  --data-root ~/data/recruit --checkpoints artifacts/runs/local \
  --test-start 2017-02-01 --seed 42
```

Or via the Makefile: `make smoke` (synthetic), `make train` (real data),
`make train-full` (adds the rolling-origin backtest).

## Triggering remote training from the CLI

Yes - you can drive Colab entirely from your terminal, without opening a
browser notebook. The pipeline is identical in every path below; only the
trigger differs. **Google ships an official Colab CLI**
(github.com/googlecolab/google-colab-cli, announced June 2026; Linux/macOS),
so Colab itself is now the recommended path.

### Path A - Official Colab CLI (recommended)

One-time setup:

```bash
uv tool install google-colab-cli    # or: pip install google-colab-cli
colab new                           # first run opens the browser OAuth flow
colab stop
```

Every run after that is fully headless - the Makefile wires it up:

```bash
make colab-train                                  # CPU session, Drive-backed
make colab-train REPO_URL=https://github.com/kelvintawe13/DemandIQ.git REPO_REF=main
make colab-train STAGES=ingest,lightgbm           # subset of stages
make colab-train STAGES=ingest,lightgbm,backtest  # + rolling-origin backtest
make colab-train GPU=T4                           # GPU runtime (not needed)
```

What the recipe does: generates the run config locally
(`scripts/colab/make_config.py`), provisions or reuses a session (`colab new`
is skipped if one is active), mounts Drive, uploads the config, and executes
`scripts/colab/train_remote.py` on the VM via `colab exec`.

`train_remote.py` mirrors the notebook exactly: clones the repo at a pinned
ref, installs the package, fetches the Kaggle data into Drive if missing
(idempotent), then runs `run_pipeline` against the Drive checkpoint store -
so a CLI run **resumes the same checkpoints** as a browser notebook run.

Useful session commands while a run is live:

```bash
colab status -s diq                     # hardware + state
colab log -s diq -n 50                  # last 50 lines of execution history
colab url -s diq                        # open the live session in the browser
```

One-shot ephemeral alternative (fresh VM, teardown on completion, no Drive
required - results pulled back before stop):

```bash
colab run --keep scripts/colab/train_remote.py
colab download -s <session> /content/artifacts/runs/colab-cli ./artifacts-run
colab stop -s <session>
```

### Path B - Kaggle kernels push (official CLI, no Google account quota)

Kaggle gives every account a free CLI-driven kernel with 4 CPUs / 30 GB RAM /
9 h runtime and ~20 GB output pull-back - the same "no local GPU needed"
benefit. The dataset is small: LightGBM trains in 1-3 CPU minutes here too.

One-time setup:

```bash
pip install kaggle
# reuse the same ~/.kaggle/kaggle.json token you use for the data download
```

Then:

1. Upload the Recruit data once as a private Kaggle dataset:
   `kaggle datasets create -p scripts/kaggle/data --dir-mode zip`
   (edit `dataset-metadata.json` slug first; see COLAB-KAGGLE.md).
2. Edit `scripts/kaggle/kernel-metadata.json`: your kernel id + dataset slug.
3. Push and follow:

```bash
make kaggle-push            # kaggle kernels push -p scripts/kaggle
kaggle kernels status <user>/demandiq-train   # poll
kaggle kernels output <user>/demandiq-train -p artifacts/runs/kaggle
```

`run_train.py` (the kernel entry) clones the repo at a pinned ref, installs
the package, and runs `run_pipeline` against the attached dataset. Kernel
outputs become the same artifact bundle the Colab notebook produces, so the
checkpoint store, manifests, and downstream analysis are unchanged.

Alternative to cloning: `kaggle kernels pull <user>/demandiq-train -p .`
fetches the pushed source back; the kernel can also read code from an attached
dataset snapshot instead of GitHub (useful for private repos without tokens).

### Path C - Colab via SSH tunnel (community tooling)

Pre-CLI workaround, kept for completeness: a notebook cell starts an SSH
tunnel (colab-ssh + ngrok/cloudflared token), your terminal connects and runs
the same commands as locally. Works, but the session still dies on Colab's
recycle schedule and the tooling is unofficial - Path A supersedes it.

### Path D - GitHub Actions (fully supported CI compute)

The most reproducible CLI: a workflow that installs the package, downloads the
data via the Kaggle API from a repo secret, and runs the pipeline on
`ubuntu-latest` (2-4 CPUs, up to 6 h). Commit the artifacts or upload them as
workflow artifacts. The `lightgbm` stage fits comfortably in the free tier.

### Choosing

| Path | Official? | Setup | Best for |
| --- | --- | --- | --- |
| Colab CLI (`colab`) | yes (Google) | ~5 min | Day-to-day training; same Drive checkpoints as the notebook |
| Kaggle kernels push | yes (`kaggle` CLI) | ~10 min | Extra free quota; no Colab compute-unit spend |
| Colab + SSH tunnel | no (community) | ~15 min | Legacy; use Path A instead |
| GitHub Actions | yes (`gh workflow run`) | ~20 min | Scheduled/PR-triggered reproducible runs |

The checkpoint store means all paths produce identical artifacts given the
same seed, data, and code ref - pick whichever trigger fits your workflow.

## Cost / quota notes

- Free Colab is sufficient: no GPU is required for the Recruit-scale
  gradient boosting (LightGBM on 250k rows trains in ~1-3 CPU minutes);
  the notebook does not request one.
- Runtime recycling loses `/content` but never Drive; checkpoints make
  recycling a non-event. The `lightgbm` stage is the longest (~2-5 min on
  the free tier) - everything before it resumes instantly.
- Drive usage: ~25 MB data + a few MB artifacts.
