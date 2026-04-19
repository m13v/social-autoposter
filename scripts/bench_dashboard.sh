#!/usr/bin/env bash
# bench_dashboard.sh
#
# Benchmark Social Autoposter dashboard endpoint latencies using curl + awk.
# Prints a table with p50/p95/min/max per endpoint.
#
# Env vars:
#   BASE_URL     default http://localhost:3141
#   RUNS         default 10 (requests per endpoint)
#   CONCURRENCY  default 1 (serial). If >1, uses background curls + wait.
#
# Always exits 0.

set -u

BASE_URL="${BASE_URL:-http://localhost:3141}"
RUNS="${RUNS:-10}"
CONCURRENCY="${CONCURRENCY:-1}"

ENDPOINTS=(
  "/"
  "/api/pending"
  "/api/activity/stats?hours=24"
  "/api/style/stats?hours=24"
  "/api/status"
  "/api/jobs"
)

TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"

printf 'bench_dashboard.sh  time=%s  base=%s  runs=%s  concurrency=%s\n' \
  "$TIMESTAMP" "$BASE_URL" "$RUNS" "$CONCURRENCY"
printf '\n'

# Header row
printf '%-40s %-3s %-7s %-7s %-7s %-7s %s\n' \
  "endpoint" "n" "p50" "p95" "min" "max" "codes"

TMPDIR="$(mktemp -d -t bench_dashboard.XXXXXX)"
trap 'rm -rf "$TMPDIR"' EXIT

# Run one curl, append "http_code time_total" to the given outfile.
# Args: URL OUTFILE
run_one() {
  local url="$1"
  local out="$2"
  # -s silent, -o /dev/null discard body, -w format.
  # On connection failure curl prints "000 0.000".
  local line
  line="$(curl -s -o /dev/null -w '%{http_code} %{time_total}\n' \
    --max-time 60 "$url" 2>/dev/null || true)"
  if [ -z "$line" ]; then
    line="000 0.000"
  fi
  printf '%s\n' "$line" >> "$out"
}

for ep in "${ENDPOINTS[@]}"; do
  url="${BASE_URL}${ep}"
  outfile="${TMPDIR}/out.$$.$(echo "$ep" | tr -c 'A-Za-z0-9' '_')"
  : > "$outfile"

  if [ "$CONCURRENCY" -le 1 ]; then
    i=0
    while [ "$i" -lt "$RUNS" ]; do
      run_one "$url" "$outfile"
      i=$((i + 1))
    done
  else
    # Launch in waves of CONCURRENCY until RUNS total are done.
    launched=0
    while [ "$launched" -lt "$RUNS" ]; do
      wave=0
      pids=""
      while [ "$wave" -lt "$CONCURRENCY" ] && [ "$launched" -lt "$RUNS" ]; do
        run_one "$url" "$outfile" &
        pids="$pids $!"
        wave=$((wave + 1))
        launched=$((launched + 1))
      done
      # wait for this wave
      for p in $pids; do
        wait "$p" 2>/dev/null || true
      done
    done
  fi

  # Compute stats with awk.
  # Input: lines of "CODE TIME". Output one line:
  #   count p50 p95 min max codes
  awk -v ep="$ep" '
    {
      code = $1
      t = $2 + 0
      times[NR] = t
      codes[code]++
      n++
      if (n == 1 || t < mn) mn = t
      if (n == 1 || t > mx) mx = t
    }
    END {
      if (n == 0) {
        printf "%-40s %-3d %-7s %-7s %-7s %-7s %s\n", ep, 0, "-", "-", "-", "-", "none"
        exit
      }
      # sort times ascending (insertion sort, fine for small n)
      for (i = 2; i <= n; i++) {
        v = times[i]; j = i - 1
        while (j >= 1 && times[j] > v) { times[j+1] = times[j]; j-- }
        times[j+1] = v
      }
      # p50 and p95 using nearest-rank, 1-indexed
      p50_idx = int((50/100) * n + 0.9999); if (p50_idx < 1) p50_idx = 1; if (p50_idx > n) p50_idx = n
      p95_idx = int((95/100) * n + 0.9999); if (p95_idx < 1) p95_idx = 1; if (p95_idx > n) p95_idx = n
      p50 = times[p50_idx]
      p95 = times[p95_idx]

      # Build codes string sorted by code key
      ncodes = 0
      for (c in codes) { ncodes++; ck[ncodes] = c }
      for (i = 2; i <= ncodes; i++) {
        v = ck[i]; j = i - 1
        while (j >= 1 && ck[j] > v) { ck[j+1] = ck[j]; j-- }
        ck[j+1] = v
      }
      codes_str = ""
      for (i = 1; i <= ncodes; i++) {
        sep = (i == 1) ? "" : " "
        codes_str = codes_str sep ck[i] "x" codes[ck[i]]
      }

      printf "%-40s %-3d %-7.3f %-7.3f %-7.3f %-7.3f %s\n", \
        ep, n, p50, p95, mn, mx, codes_str
    }
  ' "$outfile"
done

exit 0
