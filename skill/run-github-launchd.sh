#!/bin/bash
# run-github-launchd.sh — detach wrapper invoked by launchd.
#
# Why this exists:
#   launchd's StartInterval silently SUPPRESSES a scheduled fire when the prior
#   invocation of the same Label is still alive. The github cycle does a T0
#   issue search, sleeps ~600s for momentum, then re-fetches and posts. Total
#   runtime regularly exceeds 15 min, so without this wrapper roughly half of
#   the scheduled fires got dropped.
#
# How it works:
#   Python double-fork daemon idiom — first fork gives launchd a parent that
#   exits immediately (so the job is marked complete in milliseconds), setsid
#   detaches the session, second fork prevents reacquiring a controlling
#   terminal, then we exec the real pipeline. macOS lacks `setsid(1)` and
#   `nohup ... & disown` is not enough because launchd reaps the wrapper's
#   pgid, taking the nohup child with it.
#
# Cross-cycle safety:
#   post_github.py applies an already_posted filter against the posts table
#   before drafting, so overlapping cycles will not double-post the same
#   issue. gh CLI is API-only (no shared browser/profile), so there is no
#   browser-level lock to coordinate.

REPO_DIR="$HOME/social-autoposter"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"

SCRIPT="$REPO_DIR/skill/run-github.sh"
OUT="$LOG_DIR/launchd-github-stdout.log"
ERR="$LOG_DIR/launchd-github-stderr.log"

# Preflight (added 2026-05-02): skip cleanly if Claude is blocked on a
# quota cap, or if the system is under memory pressure. See
# scripts/preflight.sh for full design.
SA_PREFLIGHT_SCRIPT="run-github"
source "$REPO_DIR/scripts/preflight.sh"
preflight_skip_if_claude_blocked
preflight_skip_if_jetsam_pressure

exec /usr/bin/python3 -c "
import os, sys
script  = '$SCRIPT'
out_log = '$OUT'
err_log = '$ERR'

if os.fork() != 0:
    os._exit(0)
os.setsid()

if os.fork() != 0:
    os._exit(0)

os.chdir('/')
out_fd = os.open(out_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
err_fd = os.open(err_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
nul_fd = os.open('/dev/null', os.O_RDONLY)
os.dup2(nul_fd, 0)
os.dup2(out_fd, 1)
os.dup2(err_fd, 2)
os.execv(script, [script])
"
