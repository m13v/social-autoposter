#!/usr/bin/env bash
# stats-linkedin.sh — LinkedIn-only stats (API + CDP)

[ -f "$HOME/.social-paused" ] && echo "PAUSED: ~/.social-paused exists, skipping run." && exit 0
exec "$(dirname "$0")/stats.sh" --platform linkedin
