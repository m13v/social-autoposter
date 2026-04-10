#!/usr/bin/env bash
# engage-moltbook-all.sh — Moltbook-only engagement (phases B, D, E)

[ -f "$HOME/.social-paused" ] && echo "PAUSED: ~/.social-paused exists, skipping run." && exit 0
exec "$(dirname "$0")/engage.sh" --platform moltbook
