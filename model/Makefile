# DemandIQ ML - local development & training
#
#   make setup        create venv + install dev deps
#   make test         run the test suite
#   make smoke        full pipeline on synthetic data (no download needed)
#   make train        train on real data (DATA_ROOT, default ./data/recruit)
#   make train-full   train + rolling-origin backtest
#   make waste        URBAN-WASTE descriptive analysis (Section 3.21)
#   make bundle       export the API serving bundle (backend MODEL_BUNDLE_PATH)
#   make kaggle-push  push training to a Kaggle kernel (free CLI compute)
#   make colab-train  run training on a Colab VM via the official Colab CLI
#   make chapter4     render the Chapter 4 draft from the run artifacts
#   make clean

DATA_ROOT ?= data/recruit
CKPT_DIR ?= artifacts/runs/latest
TEST_START ?= 2017-02-01
SEED ?= 42
KAGGLE_KERNEL ?= kelvin567890kl/demandiq-train
KAGGLE_DATA ?= kelvin567890kl/recruit-restaurant-visitor-forecasting

# --- Colab CLI (official: github.com/googlecolab/google-colab-cli) ---------
# One-time: `uv tool install google-colab-cli`, then `colab new` once in a
# terminal to complete the browser OAuth. Afterwards, fully remote runs:
#
#   make colab-train                                  # CPU, Drive-backed
#   make colab-train REPO_URL=https://github.com/kelvintawe12/DemandIQ.git
#   make colab-train STAGES=ingest,lightgbm           # subset
#   make colab-train STAGES=ingest,lightgbm,backtest  # + rolling-origin
#   make colab-train GPU=T4                           # GPU runtime (not needed)
#
# One-shot ephemeral alternative (fresh VM, teardown after):
#   colab run --keep scripts/colab/train_remote.py
REPO_URL ?= https://github.com/kelvintawe12/DemandIQ.git
REPO_REF ?= main
COLAB_SESSION ?= diq
COLAB_GPU ?=
STAGES ?=

PY := .venv/bin/python
KAGGLE := .venv/bin/kaggle

.PHONY: help setup test smoke train train-full audit waste bundle kaggle-push kaggle-stage kaggle-data kaggle-status kaggle-pull colab-train chapter4 chapter4-check clean

help:
	@grep -E '^#   make' Makefile | sed 's/^#   //'

setup:
	uv venv --python 3.11 .venv
	uv pip install -e ".[dev]" pyarrow tabulate --python .venv/bin/python

test:
	$(PY) -m pytest

smoke:
	$(PY) scripts/make_synth.py /tmp/diq-smoke
	$(PY) -m diq_model.train train --data-root /tmp/diq-smoke \
		--checkpoints artifacts/runs/smoke --test-start 2016-03-15 --no-strict

audit:
	$(PY) -m diq_model.train audit --data-root $(DATA_ROOT)

# URBAN-WASTE Food Tracking Device: Section 3.21 descriptive statistics.
# Data: data/urban-waste/Food Tracking Device dataset.csv (Zenodo 3355300).
waste:
	$(PY) scripts/analyze_waste.py --artifacts $(CKPT_DIR)

# Export the serving bundle the backend API loads (see MODEL_BUNDLE_PATH).
bundle:
	$(PY) scripts/export_serving_bundle.py --artifacts $(CKPT_DIR)

train:
	$(PY) -m diq_model.train train --data-root $(DATA_ROOT) \
		--checkpoints $(CKPT_DIR) --test-start $(TEST_START) --seed $(SEED)

train-full:
	$(PY) -m diq_model.train train --data-root $(DATA_ROOT) \
		--checkpoints $(CKPT_DIR) --test-start $(TEST_START) --seed $(SEED) \
		--with-backtest

# CLI-driven training on a free Kaggle kernel (4 CPUs / 30 GB / 9 h), no
# GitHub needed. `kernels push` uploads ONLY the code file, so the model/
# tree (payload/code.zip) and provenance (payload/commit.txt) ride in the
# private dataset, versioned on every push alongside the Recruit CSVs.
# Results come back via `kaggle kernels output`.
#   make kaggle-push      stage payload + dataset version + push kernel
#   make kaggle-stage     stage payload into the dataset dir only
#   make kaggle-status    poll a running kernel
#   make kaggle-pull      download artifacts from the finished kernel
kaggle-push: kaggle-stage kaggle-data
	$(KAGGLE) kernels push -p scripts/kaggle
	$(MAKE) kaggle-status

kaggle-stage:
	mkdir -p scripts/kaggle/data/payload
	cd .. && zip -qr model/scripts/kaggle/data/payload/code.zip model \
		-x "model/.venv/*" "model/artifacts/*" "model/data/*" \
		-x "model/scripts/kaggle/data/*" "model/.pytest_cache/*" \
		-x "model/.ruff_cache/*" "model/.coverage" "*__pycache__*" && cd model
	sha=$$(git rev-parse HEAD 2>/dev/null || echo no-commit); test -z "$$(git status --porcelain)" || sha="$$sha+dirty"; echo "$$sha" > scripts/kaggle/data/payload/commit.txt
	@echo "payload staged: $$(unzip -l scripts/kaggle/data/payload/code.zip | tail -1) | source: $$(cat scripts/kaggle/data/payload/commit.txt)"

# Upload (first time) or version (every run) the staging dir - Recruit CSVs
# plus the payload - as a private dataset the kernel attaches. Kaggle
# datasets are immutable, so each push is a new version: exactly the
# provenance we want. Metadata lives in scripts/kaggle/data/dataset-metadata.json.
kaggle-data:
	@test -f scripts/kaggle/data/dataset-metadata.json || \
		{ echo 'scripts/kaggle/data/dataset-metadata.json missing'; exit 1; }
	@test -f data/recruit/air_visit_data.csv || \
		{ echo 'data/recruit/*.csv missing - download the Recruit data first'; exit 1; }
	cp data/recruit/*.csv scripts/kaggle/data/
	rm -f scripts/kaggle/data/*.zip
	if $(KAGGLE) datasets status $(KAGGLE_DATA) >/dev/null 2>&1; then \
		$(KAGGLE) datasets version -p scripts/kaggle/data --dir-mode zip -m "refresh"; \
	else \
		$(KAGGLE) datasets create -p scripts/kaggle/data --dir-mode zip; \
	fi

kaggle-status:
	$(KAGGLE) kernels status $(KAGGLE_KERNEL)

kaggle-pull:
	mkdir -p artifacts/runs/kaggle
	$(KAGGLE) kernels output $(KAGGLE_KERNEL) -p artifacts/runs/kaggle

# Remote Colab training via the official CLI. Config travels with the script
# (`colab exec` runs it on the VM, where local env vars do not exist), so
# make_config.py generates it here and it is uploaded as /content/diq_config.json.
colab-train:
	$(PY) scripts/colab/make_config.py /tmp/diq_config.json
	colab status -s $(COLAB_SESSION) >/dev/null 2>&1 || colab new -s $(COLAB_SESSION) $(if $(COLAB_GPU),--gpu $(COLAB_GPU),)
	colab drivemount -s $(COLAB_SESSION)
	colab upload -s $(COLAB_SESSION) /tmp/diq_config.json /content/diq_config.json
	colab exec -s $(COLAB_SESSION) -f scripts/colab/train_remote.py
	@echo ""
	@echo "Artifacts: Drive MyDrive/demandiq/ (survive colab stop)"
	@echo "Ephemeral fallback: colab download -s $(COLAB_SESSION) /content/artifacts/runs/colab-cli ./artifacts-run"
	@echo "When done: colab stop -s $(COLAB_SESSION)"

# Chapter 4 draft: every number rendered from the manifest - edit the prose
# in scripts/render_chapter4.py, never the generated markdown.
chapter4:
	$(PY) scripts/render_chapter4.py --artifacts $(CKPT_DIR) --out CHAPTER4_DRAFT.md

# Fail if CHAPTER4_DRAFT.md / figures drift from the run artifacts. Regenerates
# into a temp dir and diffs; exits 0 with a SKIP when artifacts/ is absent, as
# on a fresh checkout (artifacts/ is gitignored).
chapter4-check:
	$(PY) scripts/check_chapter4_fresh.py --artifacts $(CKPT_DIR)

clean:
	rm -rf artifacts/runs /tmp/diq-smoke .pytest_cache .ruff_cache
