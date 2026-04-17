#!/usr/bin/env bash
# audit-moltbook.sh — Moltbook-only audit (Moltbook API + summary)

exec "$(dirname "$0")/audit.sh" --platform moltbook
