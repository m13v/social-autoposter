#!/usr/bin/env bash
# Download + transcribe every IG post for one creator.
# Usage: ./ig_batch_creator.sh <ig_handle>
#
# Reads URLs from scripts/ig_creators_run/<handle>/urls.txt (one post URL per line).
# Writes <shortcode>.{mp4,m4a,info.json,deepgram.json} into the same dir.
# Idempotent: skips a post if both .mp4 and .deepgram.json already exist.
# Stops the loop after N consecutive download failures (likely IG rate-limit).
set -uo pipefail

HANDLE="${1:?ig handle required}"
ROOT="/Users/matthewdi/social-autoposter/scripts/ig_creators_run"
OUT="$ROOT/$HANDLE"
URLS="$OUT/urls.txt"
LOG="$OUT/run.log"
FAIL_STREAK_LIMIT="${FAIL_STREAK_LIMIT:-5}"
SLEEP_BETWEEN="${SLEEP_BETWEEN:-3}"

[[ -f "$URLS" ]] || { echo "ERROR: $URLS missing"; exit 2; }

DEEPGRAM_API_KEY="$(grep -E '^DEEPGRAM_API_KEY=' /Users/matthewdi/fazm/web/.env.local | head -1 | cut -d= -f2-)"
[[ -n "$DEEPGRAM_API_KEY" ]] || { echo "ERROR: no DEEPGRAM_API_KEY"; exit 1; }

TOTAL=$(wc -l < "$URLS" | tr -d ' ')
echo "[start] handle=$HANDLE total=$TOTAL out=$OUT" | tee -a "$LOG"

i=0
ok=0
skipped=0
failed=0
streak=0

while IFS= read -r URL; do
  [[ -z "$URL" ]] && continue
  i=$((i+1))
  SHORT=$(echo "$URL" | sed -E 's|.*/(reel\|p)/([^/]+)/?.*|\2|')
  MP4="$OUT/${SHORT}.mp4"
  DGM="$OUT/${SHORT}.deepgram.json"
  printf "[%2d/%d] %s " "$i" "$TOTAL" "$SHORT"

  if [[ -f "$MP4" && -f "$DGM" ]]; then
    echo "skip (already done)" | tee -a "$LOG"
    skipped=$((skipped+1))
    continue
  fi

  # download
  if [[ ! -f "$MP4" ]]; then
    if ! yt-dlp --cookies-from-browser chrome --no-warnings -q \
           -o "$OUT/${SHORT}.%(ext)s" --write-info-json "$URL" \
           >>"$LOG" 2>&1; then
      echo "FAIL download" | tee -a "$LOG"
      failed=$((failed+1))
      streak=$((streak+1))
      if (( streak >= FAIL_STREAK_LIMIT )); then
        echo "[stop] $streak consecutive failures, likely rate-limited" | tee -a "$LOG"
        break
      fi
      sleep "$SLEEP_BETWEEN"
      continue
    fi
  fi
  streak=0

  # audio
  AUDIO="$OUT/${SHORT}.m4a"
  if [[ ! -f "$AUDIO" ]]; then
    ffmpeg -y -loglevel error -i "$MP4" -vn -c:a copy "$AUDIO" 2>>"$LOG" \
      || ffmpeg -y -loglevel error -i "$MP4" -vn -c:a aac -b:a 96k "$AUDIO" 2>>"$LOG" \
      || { echo "FAIL audio" | tee -a "$LOG"; failed=$((failed+1)); continue; }
  fi

  # transcribe
  if [[ ! -f "$DGM" ]]; then
    HTTP=$(curl -sS -o "$DGM" -w "%{http_code}" -X POST \
      -H "Authorization: Token ${DEEPGRAM_API_KEY}" \
      -H "Content-Type: audio/m4a" \
      --data-binary "@${AUDIO}" \
      "https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true&punctuate=true&detect_language=true")
    if [[ "$HTTP" != "200" ]]; then
      echo "FAIL deepgram http=$HTTP" | tee -a "$LOG"
      failed=$((failed+1))
      continue
    fi
  fi

  DUR=$(python3 -c "import json,sys; d=json.load(open('$DGM')); print(d['metadata'].get('duration',''))" 2>/dev/null)
  echo "ok dur=${DUR}s" | tee -a "$LOG"
  ok=$((ok+1))
  sleep "$SLEEP_BETWEEN"
done < "$URLS"

echo "[done] ok=$ok skipped=$skipped failed=$failed processed=$i/$TOTAL" | tee -a "$LOG"
