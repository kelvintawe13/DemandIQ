#!/usr/bin/env bash
# DemandIQ — publish a serving bundle to the model service (docs/RAILWAY.md §10).
#
#   MODEL_SERVICE_URL=https://demandiq-model.up.railway.app \
#   BUNDLE_ADMIN_TOKEN=... \
#   bash scripts/publish_bundle.sh backend/artifacts/serving_bundle.joblib
#
# Usage: publish_bundle.sh [bundle] [url]
#   bundle   defaults to model/artifacts/runs/latest/serving_bundle.joblib
#   url      defaults to $MODEL_SERVICE_URL
#
# It publishes and then reads the file back, comparing SHA-256, so a green run
# means the service is serving exactly the bytes that were sent — not that the
# upload returned 201.
#
# Publishing does not change the API by itself: the API fetches once at startup,
# so redeploy it (`railway redeploy --service api --yes`) to serve the new export.
#
# Exit codes:
#   0  published and verified
#   1  usage or configuration problem (no URL/token, missing file, unreachable)
#   2  the service rejected the upload
#   3  the service is serving different bytes than were sent
set -euo pipefail

BUNDLE="${1:-model/artifacts/runs/latest/serving_bundle.joblib}"
URL="${2:-${MODEL_SERVICE_URL:-}}"
TOKEN="${BUNDLE_ADMIN_TOKEN:-}"

die() {
  local code="$1"
  shift
  echo "✗ $*" >&2
  exit "$code"
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | cut -d' ' -f1
  else
    shasum -a 256 | cut -d' ' -f1
  fi
}

command -v curl >/dev/null 2>&1 || die 1 "curl is required"
[ -f "$BUNDLE" ] || die 1 "no bundle at $BUNDLE — export one first (make train-model)"
[ -s "$BUNDLE" ] || die 1 "$BUNDLE is empty"
[ -n "$URL" ] || die 1 "no model service URL: pass it as the second argument or set MODEL_SERVICE_URL"
[ -n "$TOKEN" ] || die 1 "BUNDLE_ADMIN_TOKEN is not set — copy it from the model service's variables"
URL="${URL%/}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Reachability first: a wrong URL should read as a wrong URL, not as a rejected
# upload two steps later.
if ! curl -fsS --max-time 20 "$URL/healthz" -o "$TMP/health.json"; then
  die 1 "cannot reach $URL/healthz — check MODEL_SERVICE_URL and that the service is deployed"
fi

SENT="$(sha256_of < "$BUNDLE")"
SIZE="$(wc -c < "$BUNDLE" | tr -d ' ')"
echo "publishing $BUNDLE ($SIZE bytes, sha256 $SENT)"

CODE="$(
  curl -sS --max-time 120 -o "$TMP/upload.json" -w '%{http_code}' \
    -X POST \
    -H "X-Bundle-Token: $TOKEN" \
    -H "Content-Type: application/octet-stream" \
    --data-binary "@$BUNDLE" \
    "$URL/bundles"
)" || die 1 "the upload request failed — is the service still up?"

if [ "$CODE" != "201" ]; then
  echo "  service answered $CODE:" >&2
  cat "$TMP/upload.json" >&2
  echo >&2
  case "$CODE" in
    401 | 503) die 2 "token rejected ($CODE) — BUNDLE_ADMIN_TOKEN must match the service variable" ;;
    422) die 2 "the service refused the file as invalid ($CODE) — export it with scripts/export_serving_bundle.py" ;;
    *) die 2 "upload failed with HTTP $CODE" ;;
  esac
fi

# Authoritative check: whatever the service now serves must be byte-identical.
curl -fsS --max-time 120 "$URL/latest/serving_bundle.joblib" -o "$TMP/served.joblib" \
  || die 3 "the upload was accepted but /latest/serving_bundle.joblib did not download"
SERVED="$(sha256_of < "$TMP/served.joblib")"
[ "$SERVED" = "$SENT" ] || die 3 "the service is serving $SERVED, not the $SENT that was sent"

PUBLISHED="$(sed -n 's/.*"file": *"\([^"]*\)".*/\1/p' "$TMP/upload.json" | head -1)"

echo "✓ published and verified — the service serves $SERVED"
echo "  export   ${PUBLISHED:-<unnamed>}"
echo "  next     redeploy the API so it fetches the new export:"
echo "           railway redeploy --service api --yes"

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### Serving bundle published"
    echo
    echo "| Field | Value |"
    echo "|---|---|"
    echo "| Service | \`$URL\` |"
    echo "| Export | \`${PUBLISHED:-unknown}\` |"
    echo "| Bytes | $SIZE |"
    echo "| SHA-256 | \`$SENT\` |"
    echo "| Verified | the service serves the same bytes |"
    echo
    echo "Redeploy the API (\`railway redeploy --service api --yes\`) to serve it:"
    echo "the export is fetched once, at startup."
  } >> "$GITHUB_STEP_SUMMARY"
fi
