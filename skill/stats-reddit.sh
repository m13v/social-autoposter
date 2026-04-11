#!/usr/bin/env bash
# stats-reddit.sh — Reddit-only stats (API + view counts)

[ -f "$HOME/.social-paused" ] && echo "PAUSED: ~/.social-paused exists, skipping run." && exit 0
exec "$(dirname "$0")/stats.sh" --platform reddit
