#!/bin/bash
# Posts two original Reddit threads per launchd fire (separated by 30 min).
#
# Wrapper around run-reddit-threads.sh so we double daily Reddit thread
# volume (3 fires x 2 posts = 6 posts/day) without touching the main script
# or adding more launchd slots.
#
# Each invocation acquires its own pipeline lock, picks its own target via
# pick_thread_target.py (per-sub floors apply naturally), and exits.
# We use ';' (not '&&') so a NO_ELIGIBLE_TARGET on the first run still
# lets the second run try.

set -u

REPO_DIR="$HOME/social-autoposter"
SCRIPT="$REPO_DIR/skill/run-reddit-threads.sh"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
WRAPPER_LOG="$LOG_DIR/run-reddit-threads-double-$(date +%Y-%m-%d_%H%M%S).log"

echo "=== Reddit Threads DOUBLE wrapper start: $(date) ===" | tee "$WRAPPER_LOG"

echo "--- iteration 1 ---" | tee -a "$WRAPPER_LOG"
"$SCRIPT" || echo "iter1 exit=$?" | tee -a "$WRAPPER_LOG"

echo "--- sleeping 1800s before iteration 2 ---" | tee -a "$WRAPPER_LOG"
sleep 1800

echo "--- iteration 2 ---" | tee -a "$WRAPPER_LOG"
"$SCRIPT" || echo "iter2 exit=$?" | tee -a "$WRAPPER_LOG"

echo "=== Reddit Threads DOUBLE wrapper done: $(date) ===" | tee -a "$WRAPPER_LOG"
