#!/usr/bin/env bash
# End-to-end: download latest IG post from a creator, transcribe with Deepgram.
# Usage: ./ig_scrape_transcribe.sh <ig_handle> [N_CANDIDATES=3]
#
# How it works:
#   1. Loads DEEPGRAM_API_KEY from ~/fazm/web/.env.local (sibling repo).
#   2. Asks the user's logged-in Chrome (via playwright-extension MCP)
#      for the first N reel/post URLs on the profile grid. (Caller must
#      pass the URLs in via stdin, one per line — see ig_pick_latest.py
#      for the picker that does this end-to-end via MCP.)
#   3. yt-dlp fetches metadata for each candidate, sorts by upload_date,
#      keeps the most recent (this skips pinned-but-old posts).
#   4. Downloads the chosen post, extracts audio, sends to Deepgram nova-3.
#
# Standalone fallback: if no URLs on stdin, pass a single post URL as $2.
set -euo pipefail

HANDLE="${1:?ig handle required}"
SINGLE_URL="${2:-}"
OUT_DIR="/tmp/ig_scrape/${HANDLE}"
mkdir -p "$OUT_DIR"

DEEPGRAM_API_KEY="$(grep -E '^DEEPGRAM_API_KEY=' /Users/matthewdi/fazm/web/.env.local | head -1 | cut -d= -f2-)"
[[ -z "${DEEPGRAM_API_KEY}" ]] && { echo "ERROR: no DEEPGRAM_API_KEY"; exit 1; }
echo "[1/5] Deepgram key loaded (len=${#DEEPGRAM_API_KEY})"

# Collect candidate URLs
if [[ -n "$SINGLE_URL" ]]; then
  CANDIDATES=("$SINGLE_URL")
elif [[ ! -t 0 ]]; then
  mapfile -t CANDIDATES < <(grep -E 'instagram\.com/.*/(p|reel)/' || true)
else
  echo "ERROR: pass post URL as \$2 OR pipe candidate URLs on stdin" >&2
  exit 2
fi
echo "[2/5] ${#CANDIDATES[@]} candidate URL(s)"

# Sort candidates by upload_date desc using yt-dlp metadata only
BEST_URL=""
BEST_DATE=""
for U in "${CANDIDATES[@]}"; do
  D=$(yt-dlp --cookies-from-browser chrome --no-warnings -q --skip-download \
        --print "%(upload_date)s" "$U" 2>/dev/null || true)
  echo "    $D  $U"
  if [[ -n "$D" && "$D" > "${BEST_DATE:-}" ]]; then
    BEST_DATE="$D"
    BEST_URL="$U"
  fi
done
[[ -z "$BEST_URL" ]] && { echo "ERROR: no usable candidate"; exit 3; }
echo "[3/5] picked $BEST_URL (upload_date=$BEST_DATE)"

# Download the chosen post
yt-dlp --cookies-from-browser chrome --no-warnings -q \
  -o "${OUT_DIR}/%(id)s.%(ext)s" --write-info-json "$BEST_URL"
VIDEO="$(ls -t "$OUT_DIR"/*.mp4 | head -1)"
echo "    file: $VIDEO ($(du -h "$VIDEO" | cut -f1))"

# Extract audio
AUDIO="${VIDEO%.mp4}.m4a"
echo "[4/5] extracting audio"
ffmpeg -y -loglevel error -i "$VIDEO" -vn -c:a copy "$AUDIO" 2>/dev/null || \
  ffmpeg -y -loglevel error -i "$VIDEO" -vn -c:a aac -b:a 96k "$AUDIO"

# Transcribe
echo "[5/5] Deepgram nova-3"
TJ="${VIDEO%.mp4}.deepgram.json"
curl -sS -X POST \
  -H "Authorization: Token ${DEEPGRAM_API_KEY}" \
  -H "Content-Type: audio/m4a" \
  --data-binary "@${AUDIO}" \
  "https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true&punctuate=true&detect_language=true" \
  -o "$TJ"

python3 - <<PY
import json, os
d = json.load(open("$TJ"))
info = json.load(open(os.path.splitext("$VIDEO")[0] + ".info.json"))
ch = d["results"]["channels"][0]
print()
print("==================== RESULT ====================")
print(f"creator   : @${HANDLE}")
print(f"url       : {info.get('webpage_url') or info.get('original_url')}")
print(f"upload_dt : {info.get('upload_date')}")
print(f"duration  : {d['metadata'].get('duration')}s")
print(f"language  : {ch.get('detected_language','?')}")
print(f"caption   : {(info.get('description') or '')[:240]}")
print("------------------- TRANSCRIPT ------------------")
print(ch['alternatives'][0]['transcript'] or '(no speech detected)')
print("=================================================")
PY
