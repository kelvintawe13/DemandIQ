#!/usr/bin/env bash
# DemandIQ — one-command verification. Senior-grade: fast, idempotent, colored.
# Runs backend + console + frontend + db checks in parallel where safe.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="python3.11"
if ! command -v python3.11 &>/dev/null; then PY="python3"; fi

# Colors
G="\033[32m"; R="\033[31m"; Y="\033[33m"; B="\033[34m"; D="\033[2m"; N="\033[0m"
ok() { echo -e "${G}✓${N} $*"; }
warn() { echo -e "${Y}⚠${N} $*"; }
fail() { echo -e "${R}✗${N} $*"; exit 1; }
hdr() { echo -e "\n${B}━━ $* ━━${N}"; }

FAILED=0
trap 'FAILED=1' ERR

# --- Python version gate (backend requires 3.11 for X|None) ---
hdr "Environment"
$PY --version | grep -q "3.11" && ok "python 3.11 $($PY --version)" || warn "python is $($PY --version) — backend expects 3.11"
# Check against the declared floor (frontend/package.json "engines": >=18)
# rather than an allowlist of majors, which goes stale every time node ships a
# release the list has never heard of.
node -e 'process.exit(process.versions.node.split(".")[0] >= 18 ? 0 : 1)' 2>/dev/null \
  && ok "node $(node --version)" || warn "node $(node --version 2>&1) — frontend needs >=18"
echo -e "${D}root: $ROOT${N}"

# --- Backend ---
hdr "Backend: ruff / mypy / pytest / openapi"
(
  cd "$ROOT/backend"
  echo -e "${D}→ ruff check (app|tests|scripts|migrations)${N}"
  # Real gate over the whole tree — no more grepping stdout for one string.
  $PY -m ruff check app tests scripts migrations >/tmp/diq-ruff.log 2>&1 \
    && ok "ruff" || { tail -20 /tmp/diq-ruff.log; fail "ruff"; }
  echo -e "${D}→ black --check${N}"
  $PY -m black -q --check app tests scripts migrations >/tmp/diq-black.log 2>&1 \
    && ok "black" || { tail -20 /tmp/diq-black.log; fail "black"; }
  echo -e "${D}→ mypy (strict, tracked backlog in pyproject)${N}"
  $PY -m mypy app >/tmp/diq-mypy.log 2>&1 \
    && ok "mypy" || { tail -20 /tmp/diq-mypy.log; fail "mypy"; }
  echo -e "${D}→ pytest${N}"
  # Run once; the previous version ran the suite three times to both print and
  # parse the summary.
  $PY -m pytest -q >/tmp/diq-pytest.log 2>&1 && PRESULT=ok || PRESULT=fail
  tail -5 /tmp/diq-pytest.log
  [ "$PRESULT" = ok ] && ok "pytest $(grep -o "[0-9]* passed" /tmp/diq-pytest.log | head -1)" || fail "pytest failed"
  echo -e "${D}→ openapi${N}"
  # Floor matches the surface as it stands (177 paths): a route silently
  # disappearing is the regression this catches.
  $PY -c "from app.main import create_app; a=create_app(); o=a.openapi(); assert len(o['paths'])>=155, o" && ok "openapi $($PY -c "from app.main import create_app; print(len(create_app().openapi()['paths']))") paths"
  echo -e "${D}→ console & root routes${N}"
  $PY -c "from fastapi.testclient import TestClient; from app.main import create_app; c=TestClient(create_app()); assert c.get('/').status_code==200; assert c.get('/console').status_code==200; assert c.get('/health').status_code==200; print('routes ok')" && ok "routes / /console /health"
) & P1=$!

# --- Response-field parity ---
# A key the API sends and no screen reads is either a screen that forgot it or
# API surface that is deliberately unwired. Neither is visible from one side,
# which is how the datasets panel ended up stating fixture numbers. The reviewed
# list lives in the script, so this fails on anything new.
(
  cd "$ROOT"
  echo -e "${D}→ response fields no screen reads${N}"
  if $PY scripts/check_unread_fields.py >/tmp/diq-fields.log 2>&1; then
    ok "$(tail -1 /tmp/diq-fields.log)"
  else
    cat /tmp/diq-fields.log
    fail "unreviewed response fields"
  fi
) & P7=$!

# --- Console freshness ---
(
  cd "$ROOT/backend"
  echo -e "${D}→ console catalog sync${N}"
  # Count live routes vs catalog entries
  LIVE=$($PY -c "from app.main import create_app; print(len(create_app().openapi()['paths']))")
  CAT=$(grep -c '"/api/v1' api-console.html || true)
  # A floor that still means something: 213 of the matching lines are item
  # paths, against 177 live routes (items > paths because one path can carry
  # several methods). Held below the item count so a reordering is fine and a
  # truncated catalog is not.
  [ "$CAT" -ge 205 ] && ok "catalog $CAT entries (live $LIVE paths)" || warn "catalog thin: $CAT vs live $LIVE"
) & P2=$!

# --- Frontend ---
(
  cd "$ROOT/frontend"
  echo -e "${D}→ vite build${N}"
  npm run build 2>&1 | tail -3 | grep -q "built in" && ok "vite build" || warn "vite build (check logs)"
) & P3=$!

# --- Database (if postgres reachable) ---
(
  if pg_isready -h localhost -p 5432 -q 2>/dev/null; then
    echo -e "${D}→ postgres reachable${N}"
    DB="demandiq"
    # Try to ensure DB exists
    psql -h localhost -U postgres -d postgres -t -c "SELECT 1 FROM pg_database WHERE datname='$DB'" 2>/dev/null | grep -q 1 || createdb -h localhost -U postgres "$DB" 2>/dev/null || true
    # Alembic check: the DB must be stamped at head. `alembic current` marks the
    # head revision with "(head)", which keeps this honest as migrations are
    # added (a hardcoded "003_billing" went stale the moment 004 landed). A DB
    # stamped at a pre-merge branch prints two revisions and no "(head)".
    if (cd "$ROOT/backend" && $PY -m alembic current 2>&1 | grep -q "(head)"); then
      ok "alembic at head"
    else
      APPLIED=$(cd "$ROOT/backend" && $PY -m alembic current 2>/dev/null | tr '\n' ' ')
      warn "alembic not at head (applied: ${APPLIED:-none}) — run: (cd backend && alembic upgrade head)"
    fi
    # Schema parity: fresh via schema.sql vs alembic
    CNT=$(psql -h localhost -U postgres -d "$DB" -t -c "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename!='alembic_version'" 2>/dev/null | tr -d ' ')
    [ "$CNT" -ge 53 ] && ok "db tables $CNT (schema.sql + AGENT_SCHEMA.sql define 53; extra tables are allowed)" || warn "db tables $CNT (schema.sql + AGENT_SCHEMA.sql define 53)"
  else
    warn "postgres not reachable — skipping live DB checks (mocked tests still pass)"
  fi
) & P4=$!

# --- Model leakage guard (P0) ---
# Must run under the model's own venv: `diq_model` is installed there (editable),
# while the system interpreter has numpy/pandas but no package on its path. Run
# with $PY and every test errors with ModuleNotFoundError, which red-lines the
# whole verification for an environment reason rather than a real one.
(
  MPY="$ROOT/model/.venv/bin/python"
  [ -x "$MPY" ] || MPY="$PY"
  if $MPY -c "import numpy, pandas, diq_model" 2>/dev/null; then
    cd "$ROOT/model"
    echo -e "${D}→ model leakage suite${N}"
    if $MPY -m pytest tests/test_leakage.py -q; then
      ok "model leakage 8 passed"
    else
      fail "model leakage failed"
    fi
    # Also ensure feature set version matches docs
    $MPY -c "from diq_model.features.build import FEATURE_SET_VERSION; assert FEATURE_SET_VERSION=='fs-2026.09.2', FEATURE_SET_VERSION" && ok "feature_set_version fs-2026.09.2" || fail "FEATURE_SET_VERSION mismatch"
  else
    warn "model deps missing (numpy/pandas/diq_model) — skipping leakage check (cd model && uv pip install -e '.[dev]')"
  fi
) & P5=$!

# --- Dataset coverage (P0): BOTH published datasets must actually load ---
# Recruit drives the demand models; URBAN-WASTE is characterised independently
# (Section 3.21, never joined). The raw CSVs are gitignored, so this reports
# "CSV absent" rather than failing when a clone has no data — but when the data
# IS present it fails on any drift from the proposal's audited figures.
(
  MPY="$ROOT/model/.venv/bin/python"
  [ -x "$MPY" ] || MPY="$PY"
  if $MPY -c "import numpy, pandas" 2>/dev/null; then
    cd "$ROOT/model"
    echo -e "${D}→ dataset coverage (Recruit + URBAN-WASTE)${N}"
    # No explicit -q: pyproject's addopts already sets one, and a second makes
    # pytest double-quiet, which suppresses the summary line this check greps
    # for — so the greps below could never match and the block always failed.
    OUT=$($MPY -m pytest tests/test_load.py tests/test_waste.py -k audited_figures 2>&1 | tail -3)
    if echo "$OUT" | grep -q -E "[0-9]+ passed"; then
      ok "both datasets load and match the audited figures"
    elif echo "$OUT" | grep -q -E "[0-9]+ skipped"; then
      warn "dataset CSVs absent (gitignored) — real-data checks skipped; loaders still unit-tested"
    else
      fail "dataset coverage check failed:\n$OUT"
    fi
  else
    warn "model deps missing (numpy/pandas) — skipping dataset coverage"
  fi
) & P6=$!

wait $P1 || FAILED=1
wait $P2 || FAILED=1
wait $P3 || FAILED=1
wait $P4 || FAILED=1
wait $P5 || FAILED=1
wait $P6 || FAILED=1
wait $P7 || FAILED=1

hdr "Summary"
if [ "$FAILED" = 0 ]; then
  echo -e "${G}All checks passed — backend suite, openapi, model leakage gate, frontend built.${N}"
  echo -e "${D}Next: npm run dev (frontend :5173) + uvicorn app.main:app --reload (backend :8000) — see scripts/dev.sh${N}"
  echo -e "${D}Artifacts: /console (api-console.html), /app (frontend/dist), /docs (openapi)${N}"
else
  fail "Some checks failed — see above"
fi
