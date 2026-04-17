#!/usr/bin/env bash
# audit-twitter.sh — Twitter-only audit (fxtwitter API check + summary)

exec "$(dirname "$0")/audit.sh" --platform twitter
