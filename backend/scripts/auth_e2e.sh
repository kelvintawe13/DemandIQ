#!/usr/bin/env bash
# Automated auth E2E — Google OAuth, Turnstile, silent refresh, profile gate.
#
# Boots a disposable Postgres 18 cluster, runs all migrations, then drives the
# real FastAPI app over HTTP in three phases:
#   phase 1  dev-safe settings: full Google flow (local JWKS, Google wire
#            format), silent refresh, logout revocation, identities
#   phase 2  TURNSTILE_SECRET set: captcha enforcement + real Cloudflare call
#   phase 3  GOOGLE_OAUTH_CLIENT_ID set: audience enforcement + profile-gate
#            lifecycle (viewer role, complete-once, idempotent guard)
#
# Environment:
#   AUTH_E2E_PORT     bind port for the API      (default 8011)
#   AUTH_E2E_PG       postgres port to use       (default 5433)
#   AUTH_E2E_JWKS     local JWKS bind port       (default 8912)
#   TURNSTILE_SECRET / GOOGLE_OAUTH_CLIENT_ID   real creds for phases 2-3
#     (read from backend/.env when present)
#
# Usage: backend/scripts/auth_e2e.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND="$REPO_ROOT/backend"
# Python resolution: explicit override > backend/.venv > PATH python3.
# (CI uses the override; local dev keeps the project venv.)
PY="${AUTH_E2E_PYTHON:-}"
if [ -z "$PY" ]; then
  if [ -x "$BACKEND/.venv/bin/python" ]; then PY="$BACKEND/.venv/bin/python"; else PY="python3"; fi
fi
# Postgres binary discovery: explicit override > versioned server installs
# (Homebrew, Debian/Ubuntu CI) > bare psql on PATH. Server binaries (initdb,
# pg_ctl) must live alongside psql.
PG_BIN="${AUTH_E2E_PG_BIN:-}"
if [ -z "$PG_BIN" ]; then
  for cand in /usr/local/opt/postgresql@18/bin /opt/homebrew/opt/postgresql@18/bin /usr/lib/postgresql/18/bin /usr/lib/postgresql/17/bin /usr/lib/postgresql/16/bin /usr/lib/postgresql/15/bin "$(command -v psql 2>/dev/null || true)"; do
    if [ -x "$cand/psql" ]; then PG_BIN="$cand"; break; fi
    if [ -x "$cand" ] && [ "$(basename "$cand")" = "psql" ]; then PG_BIN="$(dirname "$cand")"; break; fi
  done
fi
[ -n "$PG_BIN" ] || { echo "postgres client binaries not found (set AUTH_E2E_PG_BIN)" >&2; exit 1; }
for tool in psql initdb pg_ctl pg_isready; do
  [ -x "$PG_BIN/$tool" ] || { echo "$PG_BIN/$tool missing — install the postgres server package (client-only installs lack initdb/pg_ctl)" >&2; exit 1; }
done
API_PORT="${AUTH_E2E_PORT:-8011}"
PG_PORT="${AUTH_E2E_PG:-5433}"
JWKS_PORT="${AUTH_E2E_JWKS:-8912}"
DBNAME="diq_auth_e2e"

# A stale server squatting on the API/JWKS port would answer the health
# check with old code and poison the run (this actually happened once).
for port in "$API_PORT" "$JWKS_PORT"; do
  if lsof -nP -iTCP:$port -sTCP:LISTEN >/dev/null 2>&1; then
    echo "port $port already in use — stop the stale process or set AUTH_E2E_PORT/AUTH_E2E_JWKS" >&2
    exit 1
  fi
done

# Pull real credentials from backend/.env when the caller didn't set them,
# but NEVER pick up Cloudflare's always-pass "1x000..." dummy secret: it
# accepts any token, which would turn phase 2's fails-closed check into a
# false failure (and proves nothing). A real secret must be explicit.
# Parsed line-by-line (not `source`d) so values with spaces can't execute.
if [ -f "$BACKEND/.env" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in ''|'\#'*|*'='*) ;; *) continue ;; esac
    case "$line" in '#'*|'') continue ;; esac
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in export) key="${value%%=*}"; value="${value#*=}" ;; esac
    [ -n "$key" ] || continue
    if [ -z "${!key+x}" ]; then export "$key=$value"; fi
  done < "$BACKEND/.env"
fi

echo "==> starting disposable Postgres on :$PG_PORT"
PGDATA=/tmp/diq-auth-e2e-pgdata
mkdir -p "$PGDATA"
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  "$PG_BIN/initdb" -D "$PGDATA" --auth=trust -U postgres >/dev/null
fi
rm -f "$PGDATA/postmaster.pid"
if ! "$PG_BIN/pg_isready" -h localhost -p "$PG_PORT" >/dev/null 2>&1; then
  "$PG_BIN/pg_ctl" -D "$PGDATA" -l /tmp/diq-auth-e2e-pg.log -o "-p $PG_PORT" start -w >/dev/null
fi
"$PG_BIN/psql" -h localhost -p "$PG_PORT" -U postgres -d postgres \
  -c "DROP DATABASE IF EXISTS $DBNAME" -c "CREATE DATABASE $DBNAME" >/dev/null

cleanup() {
  [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null || true
  "$PG_BIN/psql" -h localhost -p "$PG_PORT" -U postgres -d postgres \
    -c "DROP DATABASE IF EXISTS $DBNAME" >/dev/null 2>&1 || true
  "$PG_BIN/pg_ctl" -D "$PGDATA" stop -m fast >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> running migrations"
(
  cd "$BACKEND" && \
  DATABASE_URL="postgresql+psycopg2://postgres@localhost:$PG_PORT/$DBNAME" \
    "$PY" -m alembic upgrade head
) || exit 1

run_phase() {
  local phase="$1"; shift
  echo "==> phase $phase: $*"
  rm -f /tmp/diq-auth-e2e-server.log
  # Phase 1 runs with human-verification unconfigured regardless of local
  # backend/.env credentials; phases 2-3 opt in by keeping them. Empty
  # strings (not `env -u`) so a developer's backend/.env — which pydantic
  # reads when CWD is backend/ — can never leak into the dev-safe phase.
  local unset_args=""
  if [ "$phase" = "1" ]; then
    unset_args="TURNSTILE_SECRET= GOOGLE_OAUTH_CLIENT_ID= GOOGLE_OAUTH_CLIENT_SECRET="
  fi
  # shellcheck disable=SC2086
  DATABASE_URL="postgresql+psycopg2://postgres@localhost:$PG_PORT/$DBNAME" \
    PORT="$API_PORT" JWKS_URL="http://127.0.0.1:$JWKS_PORT/certs" \
    env $unset_args \
    "$PY" "$BACKEND/scripts/auth_e2e_server.py" > /tmp/diq-auth-e2e-server.log 2>&1 &
  SERVER_PID=$!
  for _ in $(seq 1 40); do
    curl -sf -o /dev/null "http://127.0.0.1:$API_PORT/health" && break
    sleep 0.5
  done
  # shellcheck disable=SC2086
  DATABASE_URL="postgresql+psycopg2://postgres@localhost:$PG_PORT/$DBNAME" \
    PORT="$API_PORT" PHASE="$phase" \
    env $unset_args \
    "$PY" "$BACKEND/scripts/auth_e2e.py"
  kill "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
  SERVER_PID=""
}

# Phases that did not run. A skipped phase must never read as a clean sweep:
# this script used to print ALL-PHASES-PASSED while phase 3 (the Google sign-in
# and sign-up path) had quietly not executed at all.
SKIPPED_PHASES=""
skip_phase() {
  local phase="$1" reason="$2"
  echo "==> phase $phase skipped ($reason)"
  SKIPPED_PHASES="${SKIPPED_PHASES:+$SKIPPED_PHASES,}$phase"
}

run_phase 1 "dev-safe settings (Turnstile/Google unconfigured)"
case "${TURNSTILE_SECRET:-}" in
  "") skip_phase 2 "TURNSTILE_SECRET not set" ;;
  1x0000000000000000000000000000000AA)
    skip_phase 2 "TURNSTILE_SECRET is the always-pass dummy; phase 2 needs a real secret"
    unset TURNSTILE_SECRET
    ;;
  *) run_phase 2 "Turnstile enforcement + real Cloudflare verdict" ;;
esac
if [ -n "${GOOGLE_OAUTH_CLIENT_ID:-}" ]; then
  run_phase 3 "Google audience enforcement + profile-gate lifecycle"
else
  skip_phase 3 "GOOGLE_OAUTH_CLIENT_ID not set"
fi

# `AUTH_E2E_REQUIRE_PHASES` names the phases that must actually run. CI sets it
# to `3`, so a missing GOOGLE_OAUTH_CLIENT_ID repo secret fails the job with the
# name of the secret to add rather than silently proving only the dev-safe
# phase. It stays unset locally, where a real Turnstile secret cannot be used
# from every network.
if [ -n "${AUTH_E2E_REQUIRE_PHASES:-}" ]; then
  IFS=',' read -r -a REQUIRED_PHASES <<< "${AUTH_E2E_REQUIRE_PHASES}"
  for phase in "${REQUIRED_PHASES[@]}"; do
    case ",${SKIPPED_PHASES}," in
      *",${phase},"*)
        echo "::error::auth E2E phase ${phase} was skipped but AUTH_E2E_REQUIRE_PHASES requires it — set TURNSTILE_SECRET for phase 2, GOOGLE_OAUTH_CLIENT_ID for phase 3" >&2
        exit 1
        ;;
    esac
  done
fi

echo ""
if [ -n "${SKIPPED_PHASES}" ]; then
  echo "AUTH-E2E-PARTIAL (skipped phase(s): ${SKIPPED_PHASES})"
else
  echo "AUTH-E2E-ALL-PHASES-PASSED"
fi
