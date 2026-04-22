#!/bin/bash
# Shared retry wrapper for `claude` CLI invocations inside pipelines.
#
# Why: Claude Code auto-updates itself mid-session by running
# `npm install -g @anthropic-ai/claude-code`, which briefly unlinks the
# binary at ~/.nvm/versions/node/<ver>/bin/claude between the old and new
# symlink. Any `claude ...` spawned during that 1-3s window errors with
# "command not found" and kills the pipeline target. Run 2 of the top-pages
# pipeline on 2026-04-22 lost 4/12 targets to this.
#
# Usage:
#   source "$(dirname "$0")/claude_helpers.sh"
#   claude_with_retry --model opus --print --output-format json < prompt.txt > out.json 2>err.log
#   claude_with_retry -p "some prompt" > out.log 2>err.log
#
# Tunables (env):
#   CLAUDE_WAIT_MAX   seconds to wait for `claude` on PATH (default 120)
#   CLAUDE_MAX_TRIES  total attempts per call (default 3)
#   CLAUDE_RETRY_SLEEP seconds between failed attempts (default 30)

# Block until `command -v claude` resolves, up to CLAUDE_WAIT_MAX seconds.
claude_wait_available() {
    local max_wait="${CLAUDE_WAIT_MAX:-120}"
    local waited=0
    while ! command -v claude >/dev/null 2>&1; do
        if [ "$waited" -ge "$max_wait" ]; then
            echo "  claude_wait: timed out after ${max_wait}s without claude on PATH" >&2
            return 1
        fi
        [ "$waited" -eq 0 ] && echo "  claude_wait: binary missing, waiting (auto-update?)" >&2
        sleep 5
        waited=$((waited + 5))
    done
    [ "$waited" -gt 0 ] && echo "  claude_wait: binary appeared after ${waited}s" >&2
    return 0
}

# Run `claude "$@"` with retries. Buffers stdin on first entry so retries
# can re-read the same input (claude consumes stdin, so a naked `< file`
# redirect would be empty on the second try).
claude_with_retry() {
    local max_tries="${CLAUDE_MAX_TRIES:-3}"
    local sleep_s="${CLAUDE_RETRY_SLEEP:-30}"
    local stdin_buf=""
    if [ ! -t 0 ]; then
        stdin_buf=$(mktemp -t claude_stdin)
        cat > "$stdin_buf"
    fi
    local try=1
    local rc=0
    while [ "$try" -le "$max_tries" ]; do
        if ! claude_wait_available; then
            [ -n "$stdin_buf" ] && rm -f "$stdin_buf"
            return 127
        fi
        if [ -n "$stdin_buf" ]; then
            claude "$@" < "$stdin_buf"
        else
            claude "$@"
        fi
        rc=$?
        if [ "$rc" -eq 0 ]; then
            [ -n "$stdin_buf" ] && rm -f "$stdin_buf"
            return 0
        fi
        if [ "$try" -lt "$max_tries" ]; then
            echo "  claude_retry: try $try/$max_tries failed (rc=$rc), sleeping ${sleep_s}s" >&2
            sleep "$sleep_s"
        fi
        try=$((try + 1))
    done
    [ -n "$stdin_buf" ] && rm -f "$stdin_buf"
    echo "  claude_retry: exhausted $max_tries tries (last rc=$rc)" >&2
    return "$rc"
}
