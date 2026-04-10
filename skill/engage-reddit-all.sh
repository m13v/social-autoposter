#!/usr/bin/env bash
# engage-reddit-all.sh — Reddit-only engagement (phases B, D, E)

[ -f "$HOME/.social-paused" ] && echo "PAUSED: ~/.social-paused exists, skipping run." && exit 0
exec "$(dirname "$0")/engage.sh" --platform reddit
