#!/usr/bin/env bash
# engage-dm-replies-reddit.sh — Reddit DM replies only

[ -f "$HOME/.social-paused" ] && echo "PAUSED: ~/.social-paused exists, skipping run." && exit 0
exec "$(dirname "$0")/engage-dm-replies.sh" --platform reddit
