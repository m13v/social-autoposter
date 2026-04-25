#!/usr/bin/env bash
# End-to-end: download latest IG post from a creator, transcribe with Deepgram.
# Usage: ./ig_scrape_transcribe.sh <ig_handle>
set -euo pipefail

HANDLE="${1:-that.girljen_}"
OUT_DIR="/tmp/ig_scrape/${HANDLE}"
mkdir -p "$OUT_DIR"

# Pull Deepgram key from fazm sibling repo
DEEPGRAM_API_KEY="$(grep -E '^DEEPGRAM_API_KEY=' /Users/matthewdi/fazm/web/.env.local | head -1 | cut -d= -f2-)"
if [[ -z "${DEEPGRAM_API_KEY}" ]]; then
  echo "ERROR: DEEPGRAM_API_KEY not found in ~/fazm/web/.env.local" >&2
  exit 1
fi
echo "[1/4] Deepgram key loaded (len=${#DEEPGRAM_API_KEY})"

# Download latest post (1 video) using cookies from logged-in Chrome
echo "[2/4] Downloading latest post for @${HANDLE} ..."
yt-dlp \
  --cookies-from-browser chrome \
  --playlist-end 1 \
  --no-warnings \
  -o "${OUT_DIR}/%(id)s.%(ext)s" \
  --write-info-json \
  "https://www.instagram.com/${HANDLE}/" 2>&1 | tail -20

VIDEO="$(ls -t "${OUT_DIR}"/*.mp4 2>/dev/null | head -1 || true)"
if [[ -z "$VIDEO" ]]; then
  echo "ERROR: no mp4 downloaded; check yt-dlp output above" >&2
  exit 2
fi
echo "    downloaded: $VIDEO ($(du -h "$VIDEO" | cut -f1))"

# Extract audio (smaller upload to Deepgram)
AUDIO="${VIDEO%.mp4}.m4a"
echo "[3/4] Extracting audio ..."
ffmpeg -y -loglevel error -i "$VIDEO" -vn -c:a copy "$AUDIO" 2>&1 || \
  ffmpeg -y -loglevel error -i "$VIDEO" -vn -c:a aac -b:a 96k "$AUDIO"
echo "    audio: $AUDIO ($(du -h "$AUDIO" | cut -f1))"

# Transcribe with Deepgram nova-3
echo "[4/4] Transcribing with Deepgram nova-3 ..."
TRANSCRIPT_JSON="${VIDEO%.mp4}.deepgram.json"
curl -sS -X POST \
  -H "Authorization: Token ${DEEPGRAM_API_KEY}" \
  -H "Content-Type: audio/m4a" \
  --data-binary "@${AUDIO}" \
  "https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true&punctuate=true&detect_language=true" \
  -o "$TRANSCRIPT_JSON"

TRANSCRIPT="$(python3 -c "import json,sys; d=json.load(open('$TRANSCRIPT_JSON')); print(d['results']['channels'][0]['alternatives'][0]['transcript'])")"
LANG="$(python3 -c "import json,sys; d=json.load(open('$TRANSCRIPT_JSON')); print(d['results']['channels'][0].get('detected_language','?'))")"
DUR="$(python3 -c "import json,sys; d=json.load(open('$TRANSCRIPT_JSON')); print(d['metadata'].get('duration','?'))")"

echo
echo "==================== RESULT ===================="
echo "creator:   @${HANDLE}"
echo "video:     $(basename "$VIDEO")"
echo "duration:  ${DUR}s"
echo "language:  ${LANG}"
echo "------------------- TRANSCRIPT ------------------"
echo "$TRANSCRIPT"
echo "================================================="
