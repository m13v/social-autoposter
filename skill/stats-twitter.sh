#!/usr/bin/env bash
# stats-twitter.sh — Twitter-only stats (API + fxtwitter)

[ -f "$HOME/.social-paused" ] && echo "PAUSED: ~/.social-paused exists, skipping run." && exit 0
exec "$(dirname "$0")/stats.sh" --platform twitter
