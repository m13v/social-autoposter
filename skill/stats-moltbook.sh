#!/usr/bin/env bash
# stats-moltbook.sh — Moltbook-only stats (API only)

[ -f "$HOME/.social-paused" ] && echo "PAUSED: ~/.social-paused exists, skipping run." && exit 0
exec "$(dirname "$0")/stats.sh" --platform moltbook
