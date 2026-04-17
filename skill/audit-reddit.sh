#!/usr/bin/env bash
# audit-reddit.sh — Reddit-only audit (API deleted/removed check + summary)

exec "$(dirname "$0")/audit.sh" --platform reddit
