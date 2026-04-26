#!/bin/bash
# Portable file locking (no flock needed)
# Usage: source lock.sh; acquire_lock "platform-name" [timeout_seconds]
#
# Multiple acquire_lock calls stack: all held locks are cleaned up on exit by
# a single trap. Acquire platform-browser locks BEFORE pipeline-specific locks
# to avoid deadlock across pipelines that share a browser profile.

# shellcheck source=lib/platform.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/platform.sh"

# Stack of currently-held lock directories, cleaned up on exit.
# Declared at source time so it survives across acquire_lock calls.
if [ -z "${_SA_LOCK_DIRS+x}" ]; then
  _SA_LOCK_DIRS=()
  _sa_release_locks() {
    local d
    # Guard against set -u + empty-array expansion (bash 3.2 macOS default).
    if [ "${#_SA_LOCK_DIRS[@]}" -gt 0 ]; then
      for d in "${_SA_LOCK_DIRS[@]}"; do
        rm -rf "$d"
      done
    fi
  }
  trap _sa_release_locks EXIT INT TERM HUP
fi

acquire_lock() {
  local name="$1"
  local timeout="${2:-3600}"
  local lock_dir="/tmp/social-autoposter-${name}.lock"
  local waited=0

  # Platform-browser locks still get the orphan-Chrome sweep on acquire (after
  # the lock is taken). Peers do NOT force-kill each other: a long-running
  # holder is the watchdog's responsibility (per-script caps in
  # scripts/watchdog_hung_runs.py), not a peer pipeline's. Prior versions
  # killed the holder's whole process group at lock_age > 600s and clobbered
  # unrelated steps (e.g. stats.sh Step 2 was SIGTERMed mid-API-call by a
  # waiting dm-replies-reddit on 2026-04-25).
  local is_browser_lock=false
  case "$name" in
    reddit-browser|linkedin-browser|twitter-browser) is_browser_lock=true ;;
  esac

  while ! mkdir "$lock_dir" 2>/dev/null; do
    # Check if lock is stale: no pid file, or holder pid is dead, or lock older than 3 hours
    local should_remove=false
    if [ ! -f "$lock_dir/pid" ]; then
      # No pid file - lock dir exists but incomplete, likely stale
      should_remove=true
    else
      local holder_pid
      holder_pid=$(cat "$lock_dir/pid" 2>/dev/null || echo "")
      if [ -z "$holder_pid" ] || ! kill -0 "$holder_pid" 2>/dev/null; then
        should_remove=true
      fi
    fi

    # Safety net: remove any lock older than 3 hours regardless. Watchdog's
    # per-script caps (45m default, 120m for stats_reddit/github-engage) will
    # SIGTERM a hung holder long before this fires; the bash trap then frees
    # the lock. This 3h ceiling only kicks in if a holder dies uncleanly
    # without the trap running and somehow keeps a live pid (rare).
    if [ -d "$lock_dir" ]; then
      local lock_age
      lock_age=$(( $(date +%s) - $(stat_mtime "$lock_dir") ))
      if [ "$lock_age" -gt 10800 ]; then
        should_remove=true
      fi
    fi

    if $should_remove; then
      echo "Removing stale $name lock"
      rm -rf "$lock_dir"
      continue
    fi

    if [ "$waited" -ge "$timeout" ]; then
      echo "Previous $name run still active after $((timeout/60))min, skipping"
      exit 0
    fi
    sleep 10
    waited=$((waited + 10))
  done

  # Write PID immediately after acquiring lock
  echo $$ > "$lock_dir/pid"
  _SA_LOCK_DIRS+=("$lock_dir")

  # Platform-browser locks: sweep orphan Chromes holding the profile. A prior
  # run may have exited without cleanly closing Chrome (parent playwright-mcp
  # dies, Chrome gets reparented to PID 1, profile stays locked). Since we
  # now hold the exclusive shell lock, any Chrome on this profile is an
  # orphan and safe to kill before the caller launches a fresh MCP session.
  if $is_browser_lock; then
    local platform="${name%-browser}"
    if pkill -f "user-data-dir=.*browser-profiles/${platform}" 2>/dev/null; then
      echo "Killed orphan Chrome holding ${platform} profile"
      sleep 1
    fi
  fi
}
