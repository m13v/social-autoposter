#!/bin/bash
# run-moltbook-launchd.sh — detach wrapper invoked by launchd.
#
# Why this exists:
#   launchd's StartInterval silently SUPPRESSES a scheduled fire when the prior
#   invocation of the same Label is still alive. The moltbook cycle has a 600s
#   T0->T1 sleep plus phased posting, so a single run takes 12-25 min and
#   regularly overlaps the next 15-min slot. Without this wrapper we lost
#   1-2 of every 4 fires.
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
#   run_moltbook_cycle.py uses already_posted_thread_ids() against the posts
#   table at pick time, so two overlapping cycles will not double-post the
#   same MoltBook thread. The 600s T0->T1 sleep further narrows the window.

REPO_DIR="$HOME/social-autoposter"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"

SCRIPT="$REPO_DIR/skill/run-moltbook.sh"
OUT="$LOG_DIR/launchd-moltbook-stdout.log"
ERR="$LOG_DIR/launchd-moltbook-stderr.log"

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
