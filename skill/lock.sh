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
    for d in "${_SA_LOCK_DIRS[@]}"; do
      rm -rf "$d"
    done
  }
  trap _sa_release_locks EXIT INT TERM HUP
fi

acquire_lock() {
  local name="$1"
  local timeout="${2:-3600}"
  local lock_dir="/tmp/social-autoposter-${name}.lock"
  local waited=0

  # Platform-browser locks get more aggressive handling: 10-min hold ceiling
  # (kill holder + sweep orphan Chrome on the profile). Prevents stuck
  # pipelines and leftover Chromes from blocking the platform indefinitely.
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

    # Safety net: remove any lock older than 3 hours regardless
    if [ -d "$lock_dir" ]; then
      local lock_age
      lock_age=$(( $(date +%s) - $(stat_mtime "$lock_dir") ))
      if [ "$lock_age" -gt 10800 ]; then
        should_remove=true
      fi
      # Platform-browser locks: force-kill holder after 10 min regardless of
      # liveness. A run that hasn't released the lock in 10 minutes is either
      # stuck on a hung MCP call or has orphaned its Chrome; either way the
      # right move is to kick it out so the next pipeline can proceed.
      if $is_browser_lock && [ "$lock_age" -gt 600 ] && ! $should_remove; then
        if [ -f "$lock_dir/pid" ]; then
          local stuck_pid
          stuck_pid=$(cat "$lock_dir/pid" 2>/dev/null || echo "")
          if [ -n "$stuck_pid" ] && kill -0 "$stuck_pid" 2>/dev/null; then
            echo "Force-killing $name holder (PID $stuck_pid, held $((lock_age/60))min)"
            # Kill the whole process tree: shell + claude + npx MCP + Chrome.
            # Bare `kill -TERM $pid` only kills the shell; its Claude/MCP
            # children get reparented to init and keep holding the MCP-hook
            # lock for minutes, blocking the next pipeline. Target the
            # process group (-pgid) plus any direct children via pkill -P.
            local stuck_pgid
            stuck_pgid=$(ps -o pgid= -p "$stuck_pid" 2>/dev/null | tr -d ' ')
            [ -n "$stuck_pgid" ] && kill -TERM "-$stuck_pgid" 2>/dev/null
            pkill -TERM -P "$stuck_pid" 2>/dev/null
            kill -TERM "$stuck_pid" 2>/dev/null
            sleep 2
            [ -n "$stuck_pgid" ] && kill -KILL "-$stuck_pgid" 2>/dev/null
            pkill -KILL -P "$stuck_pid" 2>/dev/null
            kill -KILL "$stuck_pid" 2>/dev/null
          fi
        fi
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
