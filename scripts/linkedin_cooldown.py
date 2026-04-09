#!/usr/bin/env python3
"""LinkedIn cooldown state management.

Shared cooldown file prevents cron runs from hammering LinkedIn after
rate limits, checkpoint challenges, or account restrictions.

Cooldown file: /tmp/linkedin_cooldown.json
Format: {"reason": "...", "resume_after": "ISO8601", "created_at": "ISO8601"}

Usage:
    # Check if we're in cooldown (exit 0 = clear, exit 1 = in cooldown)
    python3 linkedin_cooldown.py check

    # Set cooldown (duration in minutes)
    python3 linkedin_cooldown.py set --reason "429 rate limit" --minutes 120

    # Set cooldown until a specific time
    python3 linkedin_cooldown.py set --reason "account restricted" --until "2026-04-15T21:43:00"

    # Clear cooldown
    python3 linkedin_cooldown.py clear
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

COOLDOWN_FILE = "/tmp/linkedin_cooldown.json"


def log(msg: str) -> None:
    print(f"[linkedin-cooldown] {msg}", file=sys.stderr)


def read_cooldown() -> dict | None:
    """Read cooldown state. Returns None if no active cooldown."""
    if not os.path.exists(COOLDOWN_FILE):
        return None
    try:
        with open(COOLDOWN_FILE) as f:
            data = json.load(f)
        resume_after = datetime.fromisoformat(data["resume_after"])
        if resume_after.tzinfo is None:
            resume_after = resume_after.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if now >= resume_after:
            os.remove(COOLDOWN_FILE)
            return None
        return data
    except (json.JSONDecodeError, KeyError, ValueError):
        os.remove(COOLDOWN_FILE)
        return None


def set_cooldown(reason: str, resume_after: datetime) -> None:
    """Write cooldown state."""
    if resume_after.tzinfo is None:
        resume_after = resume_after.replace(tzinfo=timezone.utc)
    data = {
        "reason": reason,
        "resume_after": resume_after.isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(COOLDOWN_FILE, "w") as f:
        json.dump(data, f, indent=2)
    log(f"Cooldown set: {reason} (until {resume_after.isoformat()})")


def clear_cooldown() -> None:
    """Remove cooldown file."""
    if os.path.exists(COOLDOWN_FILE):
        os.remove(COOLDOWN_FILE)
        log("Cooldown cleared")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "check":
        state = read_cooldown()
        if state:
            resume = state["resume_after"]
            log(f"In cooldown: {state['reason']} (until {resume})")
            print(json.dumps(state))
            sys.exit(1)
        else:
            log("No active cooldown")
            sys.exit(0)

    elif cmd == "set":
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("cmd_")
        parser.add_argument("--reason", required=True)
        parser.add_argument("--minutes", type=int)
        parser.add_argument("--until")
        args = parser.parse_args(sys.argv[1:])

        if args.until:
            resume = datetime.fromisoformat(args.until)
            if resume.tzinfo is None:
                resume = resume.replace(tzinfo=timezone.utc)
        elif args.minutes:
            from datetime import timedelta
            resume = datetime.now(timezone.utc) + timedelta(minutes=args.minutes)
        else:
            print("ERROR: --minutes or --until required", file=sys.stderr)
            sys.exit(1)

        set_cooldown(args.reason, resume)

    elif cmd == "clear":
        clear_cooldown()

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
