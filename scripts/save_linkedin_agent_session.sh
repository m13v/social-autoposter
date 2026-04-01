#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-/Users/matthewdi/.claude/browser-sessions.json}"

PORT="$(
python3 - <<'PY'
import json, re, subprocess, urllib.request
ps = subprocess.check_output(
    ['zsh', '-lc', "ps -axww -o pid=,args= | rg '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome($| )'"],
    text=True,
)
for line in ps.splitlines():
    m = re.search(r'--remote-debugging-port=(\d+)', line)
    if not m:
        continue
    port = m.group(1)
    try:
        pages = json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=1))
    except Exception:
        continue
    if any(p.get('url', '').startswith('https://www.linkedin.com/feed/') for p in pages):
        print(port)
        raise SystemExit(0)
raise SystemExit('No live LinkedIn feed browser agent found')
PY
)"

python3 "$REPO_DIR/scripts/export_cdp_storage_state.py" \
  --port "$PORT" \
  --page-url-prefix "https://www.linkedin.com/feed/" \
  --cookie-url "https://www.linkedin.com/feed/" \
  --cookie-url "https://www.linkedin.com/" \
  --domain-filter "linkedin.com" \
  --merge \
  --backup \
  --require-cookie "li_at" \
  --out "$OUT"
