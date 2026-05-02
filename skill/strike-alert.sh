#!/usr/bin/env bash
# strike-alert.sh — sweep posts for unalerted strikes (status flipped to
# 'deleted' or 'removed') and email i@m13v.com one notification per strike.
# Idempotent via posts.strike_email_sent_at. Wired by
# launchd/com.m13v.social-strike-alert.plist (hourly).

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO_DIR/skill/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/strike-alert-$(date +%Y%m%d).log"

cd "$REPO_DIR" || exit 1

{
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) strike-alert sweep ==="
    /usr/bin/env python3 scripts/strike_alert.py --sweep
    echo
} >> "$LOG_FILE" 2>&1
