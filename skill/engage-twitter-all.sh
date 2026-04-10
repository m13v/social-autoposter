#!/usr/bin/env bash
# engage-twitter-all.sh — Twitter-only engagement (phases D, E)

[ -f "$HOME/.social-paused" ] && echo "PAUSED: ~/.social-paused exists, skipping run." && exit 0
exec "$(dirname "$0")/engage.sh" --platform twitter
