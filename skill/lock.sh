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
  #
  # Also sweep orphan playwright-mcp / node wrappers reparented to PID 1. A
  # live holder's MCP child is parented to its claude process; only true
  # orphans (parent died without running the EXIT trap, e.g. SIGKILL/OOM)
  # end up at ppid=1 and survive. The ppid==1 filter keeps a manually-
  # attached Claude session pointed at the same agent config safe: its MCP
  # child has the live claude as parent, not init. Without this sweep,
  # orphan wrappers accumulate over days and keep launchd from re-firing
  # because launchd treats the slot as still in flight.
  if $is_browser_lock; then
    local platform="${name%-browser}"
    # Chrome sweep: only kill Chromes whose top-level Chromium has been
    # reparented to launchd (ppid==1), i.e. true orphans whose parent
    # playwright-mcp died without cleanup. A LIVE peer's Chromium is parented
    # to its mcp wrapper (alive), so this filter skips it. Without the
    # ppid==1 guard, a peer that managed to acquire the lock concurrently
    # would SIGTERM the legitimate holder's Chrome and trigger crashes like
    # the GPU exit_code=15 we saw on 2026-04-28 14:12 PT.
    local chrome_pids
    chrome_pids=$(ps -A -o pid=,ppid=,command= | awk -v plat="browser-profiles/${platform}" '$2 == "1" && index($0, "user-data-dir=") > 0 && index($0, plat) > 0 {print $1}')
    if [ -n "$chrome_pids" ]; then
      echo "$chrome_pids" | xargs kill -TERM 2>/dev/null || true
      echo "Killed orphan Chrome (ppid=1) holding ${platform} profile: $(echo $chrome_pids | tr '\n' ' ')"
      sleep 1
    fi
    local mcp_pids
    mcp_pids=$(ps -A -o pid=,ppid=,command= | awk -v plat="${platform}-agent.json" '$2 == "1" && index($0, plat) > 0 {print $1}')
    if [ -n "$mcp_pids" ]; then
      echo "$mcp_pids" | xargs kill -TERM 2>/dev/null || true
      echo "Killed orphan MCP wrappers (ppid=1) for ${platform}-agent: $(echo $mcp_pids | tr '\n' ' ')"
      sleep 1
    fi
  fi
}

# Explicit early release. Use this when a long-running script only needs the
# browser for part of its run (e.g. run-twitter-cycle.sh holds the lock for
# Phase 1 scrape, releases during the 5-min T1 sleep + Phase 2a HTTP poll, then
# re-acquires before Phase 2b posting). Without this, sibling pipelines waiting
# on the same profile lock block for the full cycle even when the holder is
# only sleeping.
release_lock() {
  local name="$1"
  local lock_dir="/tmp/social-autoposter-${name}.lock"
  rm -rf "$lock_dir"
  # Rebuild the lock stack without this entry so the EXIT trap doesn't try to
  # rm it again (harmless, but keeps the stack honest if release_lock is paired
  # with a later re-acquire of the same name).
  if [ "${#_SA_LOCK_DIRS[@]}" -gt 0 ]; then
    local new_stack=()
    local d
    for d in "${_SA_LOCK_DIRS[@]}"; do
      [ "$d" != "$lock_dir" ] && new_stack+=("$d")
    done
    if [ "${#new_stack[@]}" -gt 0 ]; then
      _SA_LOCK_DIRS=("${new_stack[@]}")
    else
      _SA_LOCK_DIRS=()
    fi
  fi
}
