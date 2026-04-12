#!/usr/bin/env bash
# stats-reddit.sh — Reddit-only stats (API + view counts)

exec "$(dirname "$0")/stats.sh" --platform reddit
