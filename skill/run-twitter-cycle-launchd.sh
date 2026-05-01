#!/bin/bash
# run-twitter-cycle-launchd.sh — detach wrapper invoked by launchd.
#
# Why this exists:
#   launchd's StartCalendarInterval silently SUPPRESSES a scheduled fire when
#   the prior invocation of the same Label is still alive. The Twitter cycle
#   takes 25-45 min wall-clock (Phase 2b-gen runs SEO landing-page generation
#   per candidate, ~5-8 min each, lock-free). At a 15-min cadence that means
#   2-3 of every 4 fires get dropped on the floor; throughput collapses to
#   1 cycle/45min instead of 4 cycles/hr.
#
# How it works:
#   This wrapper is what launchd actually invokes. It uses Python's classic
#   double-fork daemon idiom to spawn the real pipeline in a fresh session
#   (os.setsid), then exits. macOS lacks the `setsid` binary and a plain
#   `nohup ... &; disown` is NOT sufficient — launchd cleans up the wrapper's
#   process group after the wrapper exits and SIGTERMs the nohup'd child too.
#   The double-fork moves the cycle into its own session/pgid that launchd
#   no longer tracks, so it survives wrapper exit.
#
# Net effect: launchd marks the job complete in milliseconds and fires the
# next :00/:15/:30/:45 slot on schedule. Multiple run-twitter-cycle.sh
# processes can now live in parallel; the twitter-browser lock
# (skill/lock.sh, 3600s timeout) handles the brief browser-using windows
# (Phase 1 scrape, Phase 2b-prep draft, Phase 2c post — minutes each), and
# Phase 0 is guarded by a Postgres advisory lock to prevent salvage-UPDATE
# races between overlapping cycles.

REPO_DIR="$HOME/social-autoposter"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"

SCRIPT="$REPO_DIR/skill/run-twitter-cycle.sh"
OUT="$LOG_DIR/launchd-twitter-cycle-stdout.log"
ERR="$LOG_DIR/launchd-twitter-cycle-stderr.log"

exec /usr/bin/python3 -c "
import os, sys
script  = '$SCRIPT'
out_log = '$OUT'
err_log = '$ERR'

# First fork — parent (launchd's tracked process) exits immediately so
# launchd marks the job complete. Child continues as session leader.
if os.fork() != 0:
    os._exit(0)
os.setsid()

# Second fork — guarantees the daemon cannot reacquire a controlling
# terminal and detaches one level further from any process-group cleanup.
if os.fork() != 0:
    os._exit(0)

# Grandchild: redirect stdio, exec the real cycle script.
os.chdir('/')
out_fd = os.open(out_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
err_fd = os.open(err_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
nul_fd = os.open('/dev/null', os.O_RDONLY)
os.dup2(nul_fd, 0)
os.dup2(out_fd, 1)
os.dup2(err_fd, 2)
os.execv(script, [script])
"
