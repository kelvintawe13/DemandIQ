# Training on Kaggle kernels from the CLI

The official Colab CLI (see COLAB.md, Path A) is the primary remote path; this
runbook is the alternative when Colab compute units are scarce. Kaggle kernels
give the exact same `run_pipeline` from your terminal on free compute
(4 CPUs / 30 GB RAM / 9 h sessions) - no local GPU, no browser notebook.

Recruit-scale reality check: the full 9-stage pipeline runs in ~7 minutes here
(the local reference run takes ~5; see [PIPELINE.md §12](PIPELINE.md)). GPU is
off in the kernel metadata because nothing in the pipeline needs it. The kernel
runs `run_pipeline` with its nine default stages — no backtest or sensitivity,
which are local opt-ins.

**This flow is verified end-to-end**: kernel v3 completed all 9 stages and its
metrics matched the local macOS run to full precision (same seed, data, code).

## How it works (one quirk to know)

`kaggle kernels push` uploads **only the code file** (`run_train.py`) - any
other files next to it are ignored. So the kernel's dependencies ride in the
attached **private dataset**, which is versioned on every push:

```
scripts/kaggle/data/
  dataset-metadata.json           # slug: <you>/recruit-restaurant-visitor-forecasting
  air_visit_data.csv ...          # the 4 Recruit CSVs (copied from data/recruit/)
  payload/code.zip                # the model/ tree, staged from the working tree
  payload/commit.txt              # "<sha>", "<sha>+dirty", or "no-commit[+dirty]"
```

On the VM, `run_train.py` locates the payload (Kaggle auto-extracts zips in
datasets, so it usually arrives as loose files), puts `model/src` on
`sys.path` (pure-Python package; the Kaggle image ships pandas/numpy/
lightgbm/catboost/shap/sklearn/pyarrow - no pip, no internet needed), and runs
the full checkpointed pipeline. Everything under `/kaggle/working` comes back
via `kaggle kernels output`.

## One-time setup (~10 minutes)

1. **Token**: kaggle.com > Settings > API > Create New Token. The CLI 2.x
   reads `~/.kaggle/access_token` (a `KGAT_...` string); the legacy
   `kaggle.json` is NOT used by CLI 2.x. Then `chmod 600 ~/.kaggle/access_token`.
2. **Username**: the token's owner may differ from any older credential -
   check with `.venv/bin/kaggle config view`. Set it in three places:
   - `scripts/kaggle/data/dataset-metadata.json` (`id`)
   - `scripts/kaggle/kernel-metadata.json` (`id`, `dataset_sources`)
   - `Makefile` (`KAGGLE_KERNEL`, `KAGGLE_DATA`)
3. **Data**: `make train`'s download step (or `kaggle competitions download`)
   so `data/recruit/*.csv` exists locally.

## Per run

```bash
cd model
make kaggle-push      # stage payload + version dataset + push kernel
# ... poll with make kaggle-status (or the push output already polls once) ...
make kaggle-pull      # artifacts -> artifacts/runs/kaggle/
```

`kaggle-push` echoes the staged payload stats and the provenance marker. The
dataset version history (kaggle.com > your dataset > Versions) is the audit
trail: each training run pins exactly one dataset version = data + code.

## Reproducibility

`payload/commit.txt` records the git sha, with a `+dirty` suffix when the
working tree had uncommitted changes (honest about what actually ran). **This
repository has no git commits**, so the marker reads `no-commit[+dirty]`; the
run's own provenance is anchored on the input-data sha256 recorded at ingest
([PIPELINE.md §15.3](PIPELINE.md)). The marker lands in the pulled artifacts as
`source_commit.txt`. Combined with the stage
manifests' seed/library records, a Kaggle run and a local run of the same
code are comparable line by line - and were verified to agree to full float
precision.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `payload code.zip not found` on the VM | Payload was next to the kernel code (ignored by `kernels push`); it must be inside the dataset (`make kaggle-stage` puts it there) |
| `Invalid Owner Id` on dataset create | Metadata `id` username differs from the token's owner - run `kaggle config view` |
| `title already in use` on create | Dataset exists; the Makefile's `datasets status` check should route to `datasets version` - if a create slips through, run `make kaggle-data` again |
| `401 Unauthorized` | Legacy `kaggle.json` with CLI 2.x - switch to `~/.kaggle/access_token` |
| Kernel ERROR, no log lines | Pull the log: `kaggle kernels output <kernel> -p artifacts/runs/kaggle`; the input-tree dump at the top of `run_train.py` shows exactly what the VM received |
