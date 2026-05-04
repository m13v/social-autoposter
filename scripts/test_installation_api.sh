#!/usr/bin/env bash
# End-to-end smoke test for the installation auth lane.
#
# Run AFTER:
#   1. ~/social-autoposter-website/scripts/migrate-installations.sql applied
#      against $DATABASE_URL.
#   2. Latest social-autoposter-website deployed to Vercel.
#
# Hits:
#   - GET  heartbeat with no header  (expect 400)
#   - POST heartbeat with header     (expect 200 + installation row)
#   - GET  heartbeat with header     (expect 200 + same installation row)
#   - GET  /api/v1/replies?limit=1   (expect 200 with install header, no bearer)
#
# No data is inserted to `replies` here; this only exercises the auth path.

set -euo pipefail

BASE_URL="${BASE_URL:-https://s4l.ai}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON_BIN="${PYTHON_BIN:-python3}"
HDR=$("$PYTHON_BIN" "$SCRIPT_DIR/identity.py" header)
echo "install_id: $("$PYTHON_BIN" -c "import base64,json,sys; d=json.loads(base64.b64decode(sys.argv[1])); print(d['install_id'])" "$HDR")"
echo "base_url:   $BASE_URL"
echo

step() { echo; echo "=== $1 ==="; }

step "1) GET /api/v1/installations/heartbeat (no header) -> expect 400"
curl -sS -w "\nHTTP %{http_code}\n" "$BASE_URL/api/v1/installations/heartbeat" | tail -20

step "2) POST /api/v1/installations/heartbeat (with header) -> expect 200"
curl -sS -w "\nHTTP %{http_code}\n" \
  -X POST \
  -H "X-Installation: $HDR" \
  -H "content-type: application/json" \
  -d '{}' \
  "$BASE_URL/api/v1/installations/heartbeat" | tail -40

step "3) GET /api/v1/installations/heartbeat (with header) -> expect 200"
curl -sS -w "\nHTTP %{http_code}\n" \
  -H "X-Installation: $HDR" \
  "$BASE_URL/api/v1/installations/heartbeat" | tail -40

step "4) GET /api/v1/replies?limit=1 (with install header, no bearer) -> expect 200"
curl -sS -w "\nHTTP %{http_code}\n" \
  -H "X-Installation: $HDR" \
  "$BASE_URL/api/v1/replies?limit=1" | tail -10

echo
echo "Done. If all 4 returned the expected codes, the install lane is wired."
