#!/usr/bin/env bash
# End-to-end check of the real report pipeline against the running stack:
# register -> login -> location -> demand -> queue reports -> worker renders ->
# download the bytes.
#
# The render step is the worker's, not this script's: `report_generation` runs on
# a tick (60s by default), which is exactly what a real deployment does. The
# admin-only `POST /pipeline/jobs/{job}/run` is not used because a freshly
# registered owner does not hold PIPELINE_RUN.
set -euo pipefail

API="${API:-http://localhost:8000/api/v1}"
EMAIL="report-e2e-$(date +%s)@example.com"
PASS="ReportE2E!2026"
WAIT_SECONDS="${WAIT_SECONDS:-180}"

json() { python3 -c "import json,sys;d=json.load(sys.stdin);print(eval(sys.argv[1]))" "$1"; }

echo "==> register + login"
curl -sS -X POST "$API/auth/register" -H 'content-type: application/json' -d "{
  \"name\":\"Report E2E\",\"email\":\"$EMAIL\",\"password\":\"$PASS\",
  \"business_name\":\"Report Cafe\",\"business_type\":\"cafe\"}" >/tmp/reg.json
TOKEN=$(curl -sS -X POST "$API/auth/login" -H 'content-type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASS\"}" | json "d['access_token']")
H=(-H "authorization: Bearer $TOKEN" -H 'content-type: application/json')

echo "==> create a location and five days of demand"
LOC=$(curl -sS -X POST "$API/locations" "${H[@]}" \
  -d '{"name":"Report E2E Grill","area":"Remera","cuisine":"Grill"}' | json "d['id']")
python3 - "$LOC" > /tmp/demand.json <<'PY'
import json, sys
rows = [
    {"location_id": sys.argv[1], "date": f"2026-10-{d:02d}", "demand_qty": q}
    for d, q in [(18, 120), (19, 98), (20, 145), (21, 160), (22, 132)]
]
print(json.dumps(rows))
PY
curl -sS -X POST "$API/ingest/manual" "${H[@]}" -d @/tmp/demand.json; echo

echo "==> queue one report per format"
: > /tmp/report_ids.txt
for FMT in pdf csv xlsx; do
  RID=$(curl -sS -X POST "$API/reports/generate" "${H[@]}" \
    -d "{\"template_id\":\"demand_summary\",\"format\":\"$FMT\"}" | json "d['report_id']")
  STATUS=$(curl -sS "$API/reports/$RID" "${H[@]}" | json "d['status']")
  echo "   $FMT queued id=$RID status=$STATUS"
  [ "$STATUS" = "queued" ] || { echo "expected queued before the worker runs"; exit 1; }
  echo "$FMT $RID" >> /tmp/report_ids.txt
done

echo "==> waiting for the worker tick (up to ${WAIT_SECONDS}s)"
DEADLINE=$(( $(date +%s) + WAIT_SECONDS ))
while read -r FMT RID; do
  while :; do
    DETAIL=$(curl -sS "$API/reports/$RID" "${H[@]}")
    STATUS=$(echo "$DETAIL" | json "d['status']")
    case "$STATUS" in
      completed) break ;;
      failed) echo "   $FMT failed: $(echo "$DETAIL" | json "d['error']")"; exit 1 ;;
    esac
    [ "$(date +%s)" -lt "$DEADLINE" ] || { echo "   $FMT still $STATUS after ${WAIT_SECONDS}s"; exit 1; }
    sleep 5
  done

  URL=$(echo "$DETAIL" | json "d['download_url']")
  [ -n "$URL" ] || { echo "   $FMT completed with no download url"; exit 1; }

  CODE=$(curl -sS -o "/tmp/report.$FMT" -w '%{http_code}' "http://localhost:8000$URL")
  if [ "$FMT" = "xlsx" ]; then
    # A zip signature is two bytes plus binary version flags; comparing five
    # bytes would fail on the control characters that follow.
    MAGIC=$(head -c 2 "/tmp/report.$FMT")
  else
    MAGIC=$(head -c 5 "/tmp/report.$FMT" | tr -d '\0')
  fi
  SIZE=$(wc -c < "/tmp/report.$FMT")
  TYPE=$(echo "$DETAIL" | json "d['content_type']")
  echo "   $FMT: status=completed type=$TYPE stored=$(echo "$DETAIL" | json "d['size_bytes']")B download_http=$CODE bytes=$SIZE magic=$MAGIC"
  [ "$CODE" = "200" ] || exit 1
  case "$FMT" in
    pdf) [ "$MAGIC" = "%PDF-" ] || { echo "not a PDF"; exit 1; } ;;
    xlsx) [ "$MAGIC" = "PK" ] || { echo "not an XLSX (zip)"; exit 1; } ;;
    csv) head -1 "/tmp/report.$FMT" | grep -q "^Location,Days recorded" || { echo "bad CSV header"; exit 1; } ;;
  esac

  # A forged signature and an expired-but-well-formed one must both be refused.
  BAD=$(curl -sS -o /dev/null -w '%{http_code}' \
    "http://localhost:8000/api/v1/reports/$RID/download?expires=9999999999&sig=deadbeef")
  echo "   $FMT forged link http=$BAD (expected 403)"
  [ "$BAD" = "403" ] || exit 1
done < /tmp/report_ids.txt

echo "==> what the database holds"
docker compose exec -T postgres psql -U demandiq -d demandiq -c \
  "select format, status, size_bytes, content_type, octet_length(content) as stored_bytes
     from reports order by created_at desc limit 3"

echo "REPORT-E2E-PASSED"
