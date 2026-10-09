#!/usr/bin/env bash
# Ingestion E2E — real Postgres, real migrations, real round-trips, and the
# delete routes' split semantics (removed vs. stamped-and-kept).
#
# The hermetic unit suite mocks the session, which is exactly why the
# ``ingest_uploads.preview`` JSONB bug shipped: the column map written by
# ``/ingest/file/{id}/mapping`` never reached the database, so
# ``/ingest/file/{id}/commit`` answered 422 ``missing_mapping`` while the mocked
# lifecycle test stayed green. This script boots a disposable Postgres 18
# cluster, runs every migration, and drives the real FastAPI app through the
# upload -> mapping -> commit flow with a request-scoped session.
#
# It also asserts the invariant that bug violated: an in-place edit to a JSONB
# column is persisted (``MutableJSONB``).
#
# Environment:
#   INGEST_E2E_PG       postgres port to use       (default 5434)
#   INGEST_E2E_PG_BIN   postgres server binaries   (autodetected)
#   INGEST_E2E_PYTHON   python interpreter         (backend/.venv, else python3)
#
# Usage: backend/scripts/ingest_e2e.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND="$REPO_ROOT/backend"

PY="${INGEST_E2E_PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x "$BACKEND/.venv/bin/python" ]; then PY="$BACKEND/.venv/bin/python"; else PY="python3"; fi
fi

# Postgres binary discovery mirrors auth_e2e.sh: versioned server installs
# (Homebrew, Debian/Ubuntu CI) before a bare psql on PATH. Server binaries
# (initdb, pg_ctl) must live alongside psql.
PG_BIN="${INGEST_E2E_PG_BIN:-}"
if [ -z "$PG_BIN" ]; then
  for cand in /usr/local/opt/postgresql@18/bin /opt/homebrew/opt/postgresql@18/bin /usr/lib/postgresql/18/bin /usr/lib/postgresql/17/bin /usr/lib/postgresql/16/bin /usr/lib/postgresql/15/bin "$(command -v psql 2>/dev/null || true)"; do
    if [ -x "$cand/psql" ]; then PG_BIN="$cand"; break; fi
    if [ -x "$cand" ] && [ "$(basename "$cand")" = "psql" ]; then PG_BIN="$(dirname "$cand")"; break; fi
  done
fi
[ -n "$PG_BIN" ] || { echo "postgres client binaries not found (set INGEST_E2E_PG_BIN)" >&2; exit 1; }
for tool in psql initdb pg_ctl pg_isready; do
  [ -x "$PG_BIN/$tool" ] || { echo "$PG_BIN/$tool missing — install the postgres server package" >&2; exit 1; }
done

PG_PORT="${INGEST_E2E_PG:-5434}"
PGDATA=/tmp/diq-ingest-e2e-pgdata
DBNAME=diq_ingest_e2e

echo "==> starting disposable Postgres on :$PG_PORT"
mkdir -p "$PGDATA"
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  "$PG_BIN/initdb" -D "$PGDATA" --auth=trust -U postgres >/dev/null
fi
rm -f "$PGDATA/postmaster.pid"
if ! "$PG_BIN/pg_isready" -h localhost -p "$PG_PORT" >/dev/null 2>&1; then
  "$PG_BIN/pg_ctl" -D "$PGDATA" -l /tmp/diq-ingest-e2e-pg.log -o "-p $PG_PORT" start -w >/dev/null
fi
"$PG_BIN/psql" -h localhost -p "$PG_PORT" -U postgres -d postgres \
  -c "DROP DATABASE IF EXISTS $DBNAME" -c "CREATE DATABASE $DBNAME" >/dev/null

cleanup() {
  "$PG_BIN/psql" -h localhost -p "$PG_PORT" -U postgres -d postgres \
    -c "DROP DATABASE IF EXISTS $DBNAME" >/dev/null 2>&1 || true
  "$PG_BIN/pg_ctl" -D "$PGDATA" stop -m fast >/dev/null 2>&1 || true
}
trap cleanup EXIT

DATABASE_URL="postgresql+psycopg2://postgres@localhost:$PG_PORT/$DBNAME"
export DATABASE_URL

# ``head`` must resolve to exactly one revision — the merge revision
# (010_merge_heads) exists so that this line works.
echo "==> running migrations (alembic upgrade head)"
(
  cd "$BACKEND" && "$PY" -m alembic upgrade head
) || exit 1

echo "==> running ingestion, background-job, delete-route and experiments integration tests"
(
  cd "$BACKEND" && DEMANDIQ_RUN_INTEGRATION=1 "$PY" -m pytest -q \
    tests/test_ingest_integration.py tests/test_jobs_integration.py \
    tests/test_delete_endpoints_integration.py \
    tests/test_experiments_integration.py
) || exit 1

# The standalone worker is what drains queued runs when JOBS_ENABLED is off, so
# a single tick must complete against the real schema (not just import).
echo "==> smoke-testing the standalone worker (one tick)"
WORKER_OUT="$(cd "$BACKEND" && "$PY" -m app.jobs.worker --once 2>&1)" || {
  echo "$WORKER_OUT"
  echo "worker --once exited non-zero" >&2
  exit 1
}
echo "$WORKER_OUT" | grep -q "single tick complete" || {
  echo "$WORKER_OUT"
  echo "worker --once did not report a completed tick" >&2
  exit 1
}

echo ""
echo "INGEST-E2E-PASSED"
