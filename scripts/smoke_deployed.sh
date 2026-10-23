#!/usr/bin/env bash
# DemandIQ — post-deploy smoke check.
#
#   scripts/smoke_deployed.sh https://<web>.up.railway.app
#   BASE_URL=... WAIT_SECONDS=120 scripts/smoke_deployed.sh
#
# Proves four things about the running deployment, in one pass, against the WEB
# service's public URL. Everything goes through that origin, so the check also
# exercises the nginx proxy the browser actually uses:
#
#   1. the API answers (and not just the container: the whole chain)
#   2. the schema is migrated — ``/api/v1/health`` reports ``database: ok``
#   3. forecasts come from the trained bundle, not the rolling-mean stub
#      (``serving.mode == "trained"``)
#   4. the SPA shell and API docs are actually served
#
# Why (3) is a hard failure rather than a warning: the stub is a legitimate way
# to run the UI, but not what a deployment of this project claims to be. A
# deploy that quietly fell back to it would still be green everywhere else.
#
# Exit codes: 0 healthy, 1 the deployment is not ready / is wrong, 2 misuse.
#
# Environment:
#   BASE_URL        target origin (an argument wins over this)
#   WAIT_SECONDS    keep retrying the health endpoint this long (default 600:
#                   Railway builds take minutes, and CI starts this the moment
#                   the tests finish)
#   INTERVAL        seconds between retries (default 10)
set -euo pipefail

BASE="${1:-${BASE_URL:-}}"
BASE="${BASE%/}"
if [ -z "$BASE" ]; then
  echo "usage: $0 <web-base-url>   (or set BASE_URL)" >&2
  exit 2
fi

WAIT_SECONDS="${WAIT_SECONDS:-600}"
INTERVAL="${INTERVAL:-10}"

for tool in curl jq; do
  command -v "$tool" >/dev/null 2>&1 || { echo "$tool is required" >&2; exit 2; }
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

say()  { printf '\n▸ %s\n' "$*"; }
fail() { printf '\n::error::%s\n' "$*" >&2; exit 1; }

# get <path> <outfile> — prints the status code; 000 when nothing answered.
# -S surfaces TLS/DNS errors into $TMP/curl.err without aborting the script.
get() {
  curl -sS -o "$2" -w '%{http_code}' --max-time 20 "$BASE$1" 2>"$TMP/curl.err" || true
}

# ---------------------------------------------------------------------------
# 1. Wait for the API. A deploy in progress answers 502/503 from the edge, or
#    nothing at all, so this retries rather than failing on the first attempt.
# ---------------------------------------------------------------------------
say "waiting up to ${WAIT_SECONDS}s for $BASE/api/v1/health"
deadline=$(( SECONDS + WAIT_SECONDS ))
code="000"
while :; do
  code="$(get /api/v1/health "$TMP/health.json")"
  [ "$code" = "200" ] && break
  if [ "$SECONDS" -ge "$deadline" ]; then
    err="$(tr -d '\n' < "$TMP/curl.err" 2>/dev/null || true)"
    fail "API health did not return 200 within ${WAIT_SECONDS}s (last status: $code). ${err:-Is BASE_URL the web service URL?}"
  fi
  sleep "$INTERVAL"
done

jq empty < "$TMP/health.json" 2>/dev/null \
  || fail "GET $BASE/api/v1/health did not return JSON — is BASE_URL the web service, served through its nginx proxy?"

status="$(jq -r '.status // "missing"'      < "$TMP/health.json")"
database="$(jq -r '.database // "missing"'  < "$TMP/health.json")"
mode="$(jq -r '.serving.mode // "missing"'  < "$TMP/health.json")"
algorithm="$(jq -r '.serving.algorithm // "-"' < "$TMP/health.json")"
model_version="$(jq -r '.serving.version // "-"' < "$TMP/health.json")"

# ---------------------------------------------------------------------------
# 2-3. The assertions that matter.
# ---------------------------------------------------------------------------
[ "$status" = "ok" ] || fail "API reports status=$status. \`degraded\` means MODEL_BUNDLE_PATH is set but the bundle cannot be loaded — see docs/RAILWAY.md §7."

[ "$database" = "ok" ] || fail "API reports database=$database — DATABASE_URL or the pre-deploy \`alembic upgrade head\` did not work. See docs/RAILWAY.md §2."

case "$mode" in
  trained) ;;
  stub)
    fail "API is serving STUB forecasts (rolling means), not the trained model. The image is missing backend/artifacts/serving_bundle.joblib, MODEL_BUNDLE_PATH is unset, or INSTALL_EXTRAS did not include \`model\`. See docs/RAILWAY.md §7."
    ;;
  *)
    fail "API reports serving.mode=$mode — expected \`trained\`. See docs/RAILWAY.md §7."
    ;;
esac

# ---------------------------------------------------------------------------
# 4. The web tier's own obligations.
# ---------------------------------------------------------------------------
expect() {  # expect <path> <expected-status> <label> <outfile>
  local got
  got="$(get "$1" "$4")"
  [ "$got" = "$2" ] || fail "$1 returned $got, expected $2 ($3)."
  printf '  %-22s %s  %s\n' "$1" "$got" "$3"
}

say "checking the web tier"
expect /healthz       200 "container liveness"          "$TMP/healthz.txt"
expect /app/          200 "SPA shell"                   "$TMP/index.html"
expect /docs          200 "OpenAPI docs via the proxy"  "$TMP/docs.html"

grep -q 'id="root"' "$TMP/index.html" \
  || fail "/app/ returned HTML without the SPA mount point (id=\"root\") — the frontend bundle is missing from the image."

# ---------------------------------------------------------------------------
# Report. The workflow turns $GITHUB_STEP_SUMMARY into the run's summary page.
# ---------------------------------------------------------------------------
summary="$(
  printf '| Check | Result |\n|---|---|\n'
  printf '| Web origin | `%s` |\n' "$BASE"
  printf '| API health | `%s` |\n' "$status"
  printf '| Database | `%s` |\n' "$database"
  printf '| Serving | `%s` — `%s` / `%s` |\n' "$mode" "$algorithm" "$model_version"
  printf '| SPA, /docs, /healthz | 200 |\n'
)"
printf '\n✓ %s\n%s\n' "deployment is healthy" "$summary"

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### Deployed smoke check passed"
    echo
    echo "$summary"
  } >> "$GITHUB_STEP_SUMMARY"
fi
