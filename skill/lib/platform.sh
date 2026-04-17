#!/bin/bash
# Portable platform detection for social-autoposter shell scripts.
# Source this file, then use: $PLATFORM, stat_mtime <path>, platform_notify <title> <msg>.

if [ -z "${PLATFORM:-}" ]; then
  case "$(uname -s)" in
    Darwin) PLATFORM=darwin ;;
    Linux)  PLATFORM=linux ;;
    *)      PLATFORM=unknown ;;
  esac
fi

stat_mtime() {
  local f="$1"
  case "$PLATFORM" in
    darwin) stat -f %m "$f" 2>/dev/null || echo 0 ;;
    linux)  stat -c %Y "$f" 2>/dev/null || echo 0 ;;
    *)      echo 0 ;;
  esac
}

platform_notify() {
  local title="$1"
  local msg="$2"
  case "$PLATFORM" in
    darwin)
      osascript -e "display notification \"$msg\" with title \"$title\" sound name \"Glass\"" 2>/dev/null || true
      ;;
    linux)
      if command -v notify-send >/dev/null 2>&1; then
        notify-send "$title" "$msg" 2>/dev/null || true
      fi
      ;;
  esac
}

export PLATFORM
