# DemandIQ — root Makefile
#
# Every change should pass:  make verify
#   (= lint-backend, test-backend, check-fields, auth-e2e,
#      test-backend-integration, lint-frontend, typecheck-frontend,
#      typecheck-shared, test-frontend, test-model (leakage gate),
#      build-frontend)
#
# Background work is opt-in at runtime: either set JOBS_ENABLED=true so the API
# schedules the jobs in-process, or run `make worker` as its own process. Both
# paths drive the same tick (reap dead runs, drain the queue, run due jobs).
#
# The auth E2E (backend/scripts/auth_e2e.sh) boots its own disposable
# Postgres cluster, runs every migration, and drives the real FastAPI app
# over HTTP — no docker-compose or external services required.

BACKEND_DIR := backend
FRONTEND_DIR := frontend
MODEL_DIR := model
# Prefer the project venv; fall back to system python (CI installs with pip).
# Absolutized so recipes that `cd backend` first still resolve it.
PY_REL := $(shell test -x "$(BACKEND_DIR)/.venv/bin/python" && echo "$(BACKEND_DIR)/.venv/bin/python" || echo "python3")
PY := $(if $(filter python3,$(PY_REL)),python3,$(abspath $(PY_REL)))
# Model package has its own venv (make -C model setup); CI installs model[dev,training].
MODEL_PY_REL := $(shell test -x "$(MODEL_DIR)/.venv/bin/python" && echo "$(MODEL_DIR)/.venv/bin/python" || echo "python3")
MODEL_PY := $(if $(filter python3,$(MODEL_PY_REL)),python3,$(abspath $(MODEL_PY_REL)))

# Lint scope: the WHOLE backend tree. It used to be a frozen eight-file list,
# which meant ruff/black covered ~7% of the backend and nothing said so. The
# tree now passes, so the gate can be honest about its reach.
LINT_PATHS := app tests scripts migrations

.PHONY: help install install-backend install-frontend auth-e2e \
	test-backend test-backend-integration test-frontend e2e-smoke e2e-image test-model lint-backend \
	check-fields lint-frontend typecheck-frontend typecheck-shared build-frontend worker worker-once \
	verify clean model-waste train-model datasets chapter4-check \
	docker-build docker-up docker-down docker-ps docker-logs docker-config docker-lint \
	docker-stores docker-dev docker-worker docker-migrate docker-db docker-nuke

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: install-backend install-frontend ## Install everything

install-backend: ## Backend venv + deps (incl. model serving extras)
	python3 -m venv $(BACKEND_DIR)/.venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e "$(BACKEND_DIR)[dev,model]"

install-frontend: ## Frontend deps (npm ci when the lockfile lands)
	cd $(FRONTEND_DIR) && \
		if [ -f package-lock.json ]; then \
			npm ci --no-audit --no-fund; \
		else \
			npm install --no-audit --no-fund; \
		fi

auth-e2e: ## Automated auth E2E — disposable Postgres, migrations, real HTTP
	bash $(BACKEND_DIR)/scripts/auth_e2e.sh

test-backend: ## Backend unit suite (hermetic — no DB needed)
	cd $(BACKEND_DIR) && $(PY) -m pytest -q

test-backend-integration: ## Ingestion E2E against disposable Postgres (real migrations + sessions)
	bash $(BACKEND_DIR)/scripts/ingest_e2e.sh

test-frontend: ## Frontend vitest suite
	cd $(FRONTEND_DIR) && npm test

e2e-smoke: ## Playwright route smoke against a running stack (fails on non-2xx API or console error)
	cd $(FRONTEND_DIR) && node e2e/route-smoke.mjs

e2e-image: ## Playwright image upload + OCR preview against a running stack
	cd $(FRONTEND_DIR) && node e2e/image-ingest.mjs

test-model: ## Model unit suite incl. the non-negotiable leakage gate
	cd $(MODEL_DIR) && $(MODEL_PY) -m pytest -q

# The offline retrain path, end to end: every pipeline stage (ingest → features →
# fallbacks → boosters → conformal → shap → rolling-origin backtest) plus the
# serving-bundle export the API loads. This is the ONLY way a model version
# reaches the registry — the API refuses to train on purpose (`POST /models/train`
# answers 501), because training belongs to the model/ package, not a request.
# Deterministic: the same seed reproduces the same metrics and the same versions.
# Override any input, e.g. `make train-model TRAIN_SEED=7`.
TRAIN_DATA_ROOT ?= data/recruit
TRAIN_TEST_START ?= 2017-02-01
TRAIN_SEED ?= 42
# Ceiling on the bounded validation-tail search (>= 1); use --skip-tune to disable.
TRAIN_TUNE_TRIALS ?= 8
train-model: ## Retrain offline (all stages + backtest) and re-export the serving bundle
	cd $(MODEL_DIR) && $(MODEL_PY) -m diq_model.train train \
		--data-root $(TRAIN_DATA_ROOT) --checkpoints artifacts/runs/latest \
		--test-start $(TRAIN_TEST_START) --seed $(TRAIN_SEED) \
		--tune-trials $(TRAIN_TUNE_TRIALS) --with-backtest --force
	cd $(MODEL_DIR) && $(MODEL_PY) scripts/export_serving_bundle.py --artifacts artifacts/runs/latest
	@echo "Serving bundle re-exported; the API publishes its versions on the next bundle read."

model-waste: ## Characterize URBAN-WASTE (Section 3.21) into the run artifacts
	cd $(MODEL_DIR) && $(MODEL_PY) scripts/analyze_waste.py --artifacts artifacts/runs/latest

# The Chapter 4 draft and its figures are generated. This regenerates them into a
# temp dir and diffs against what is committed, so a renderer/pipeline change that
# was not regenerated fails here rather than shipping a stale report. Skips (exit 0)
# when model/artifacts is absent, which is the norm on a fresh CI checkout.
MODEL_ARTIFACTS ?= artifacts/runs/latest
chapter4-check: ## Fail if the committed Chapter 4 draft/figures drifted from the artifacts
	cd $(MODEL_DIR) && $(MODEL_PY) scripts/check_chapter4_fresh.py --artifacts $(MODEL_ARTIFACTS)

datasets: ## Confirm BOTH published datasets load (Recruit demand + URBAN-WASTE waste)
	cd $(MODEL_DIR) && $(MODEL_PY) -m pytest tests/test_load.py tests/test_waste.py -q -k "audited_figures"
	@echo "Recruit drives the demand models; URBAN-WASTE is characterised independently (never joined)."

lint-backend: ## ruff + black over the whole backend, then a real mypy
	cd $(BACKEND_DIR) && $(PY) -m ruff check $(LINT_PATHS)
	cd $(BACKEND_DIR) && $(PY) -m black -q --check $(LINT_PATHS)
	cd $(BACKEND_DIR) && $(PY) -m mypy app

check-fields: ## Response keys no frontend screen reads (reviewed list lives in the script)
	$(PY) scripts/check_unread_fields.py

lint-frontend: ## eslint over the frontend (config lives in frontend/.eslintrc.cjs)
	cd $(FRONTEND_DIR) && npm run lint

typecheck-frontend: ## tsc --noEmit over the frontend (vite does NOT typecheck)
	cd $(FRONTEND_DIR) && npm run typecheck

typecheck-shared: ## tsc over the shared types package (reuses the frontend toolchain)
	cd $(FRONTEND_DIR) && npx tsc -p ../shared/tsconfig.json

build-frontend: ## Production frontend build
	cd $(FRONTEND_DIR) && npm run build

worker: ## Standalone job worker — drains queued runs and runs due jobs
	cd $(BACKEND_DIR) && $(PY) -m app.jobs.worker

worker-once: ## One worker tick, then exit (CronJob style)
	cd $(BACKEND_DIR) && $(PY) -m app.jobs.worker --once

verify: lint-backend test-backend check-fields auth-e2e test-backend-integration lint-frontend typecheck-frontend typecheck-shared test-frontend test-model build-frontend ## Full local CI parity

# ---------------------------------------------------------------------------
# Containers
#
# `make docker-up` brings up the whole stack (postgres, redis, migrations, API,
# web). The product UI is then on :5173/ (redirecting to /app/) and the
# OpenAPI console on :5173/console.
# See DOCKER.md.
#
# Note the deliberate split: `docker-stores` starts ONLY postgres + redis, which
# is what the host-based flow in scripts/dev.sh wants (it runs alembic, uvicorn
# and vite on the host). `docker-up` is the fully containerised equivalent.
# ---------------------------------------------------------------------------

COMPOSE ?= docker compose

# The commit stamped into the frontend bundle's footer. Compose can only
# interpolate the environment and the image has no .git, so the hash is resolved
# here and handed to the build as a build arg; without it the footer shows a
# build timestamp instead. Override with `GIT_SHA=abc1234 make docker-up`.
GIT_SHA ?= $(shell git rev-parse --short HEAD 2>/dev/null)

# The only container target that needs no Docker: it walks each build context the
# way the daemon would and fails on a credential, a host artifact, or a COPY
# source that does not exist. Runs in ~1s, so it is worth running before every
# build rather than discovering the leak from a pushed image.
docker-lint: ## Static build-context check (no Docker required)
	@$(PY) scripts/check_docker_context.py

docker-config: ## Validate docker-compose.yml without building or starting
	$(COMPOSE) config --quiet && echo "compose file is valid"

docker-build: ## Build both images
	GIT_SHA=$(GIT_SHA) $(COMPOSE) build

docker-up: ## Build + start the full stack (stores, migrations, API, web)
	GIT_SHA=$(GIT_SHA) $(COMPOSE) up -d --build
	@echo "UI:      http://localhost:$${WEB_PORT:-5173}/app/"
	@echo "Console: http://localhost:$${WEB_PORT:-5173}/"
	@echo "Status:  make docker-ps"

docker-down: ## Stop the stack, keep the data volumes
	$(COMPOSE) down

docker-nuke: ## Stop the stack and DELETE the database and cache volumes
	$(COMPOSE) down -v --remove-orphans

docker-ps: ## Service status and health
	$(COMPOSE) ps

docker-logs: ## Follow logs from every service
	$(COMPOSE) logs -f

docker-stores: ## Start only postgres + redis (for the host-based dev flow)
	$(COMPOSE) up -d postgres redis

docker-dev: ## Full stack plus the vite dev server with HMR on :5174
	GIT_SHA=$(GIT_SHA) $(COMPOSE) --profile dev up -d --build

docker-worker: ## Also run the background-job worker (opt-in profile)
	$(COMPOSE) --profile worker up -d

# `run --rm` rather than `exec`: a one-shot migration should get a fresh
# container, not be attached to whichever API container happens to be up.
docker-migrate: ## Apply migrations in a one-off container
	$(COMPOSE) run --rm migrate

docker-db: ## psql against the containerised database
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-demandiq} -d $${POSTGRES_DB:-demandiq}

clean: ## Remove build/test residue (keeps venvs and model artifacts)
	rm -rf $(FRONTEND_DIR)/dist $(FRONTEND_DIR)/node_modules/.vite
	rm -rf /tmp/diq-auth-e2e-pgdata /tmp/diq-auth-e2e-pg.log \
		/tmp/diq-auth-e2e-server.log /tmp/auth_e2e_run.log \
		/tmp/diq-ingest-e2e-pgdata /tmp/diq-ingest-e2e-pg.log
	find $(BACKEND_DIR) -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find $(BACKEND_DIR) -name '.pytest_cache' -type d -prune -exec rm -rf {} + 2>/dev/null || true
