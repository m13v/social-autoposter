#!/usr/bin/env python3
"""DM helper: upsert profile, set ICP for all projects, log send in one call."""
import argparse
import os
import sys
import subprocess

PROJECTS = ["fazm", "Terminator", "macOS MCP", "S4L", "AI Browser Profile", "WhatsApp MCP",
            "macOS Session Replay", "Cyrano", "Assrt", "PieLine", "Clone", "mk0r",
            "fde10x", "claude-meter", "c0nsl"]

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"ERR: {r.stderr}", file=sys.stderr)
    return r.stdout

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dm-id", required=True)
    ap.add_argument("--author", required=True)
    ap.add_argument("--headline", default="")
    ap.add_argument("--bio", default="")
    ap.add_argument("--recent", default="")
    ap.add_argument("--notes", default="")
    ap.add_argument("--icp-note", default="no strong signal", help="short ICP rationale applied to all projects")
    args = ap.parse_args()

    # upsert profile
    cmd = ["python3", "/Users/matthewdi/social-autoposter/scripts/fetch_prospect_profile.py", "upsert",
           "--platform", "reddit", "--author", args.author,
           "--profile-url", f"https://www.reddit.com/user/{args.author}/",
           "--link-dm", args.dm_id]
    if args.headline: cmd += ["--headline", args.headline]
    if args.bio: cmd += ["--bio", args.bio]
    if args.recent: cmd += ["--recent-activity", args.recent]
    if args.notes: cmd += ["--notes", args.notes]
    print(run(cmd).strip())

    # set ICP for all projects
    for proj in PROJECTS:
        out = run(["python3", "/Users/matthewdi/social-autoposter/scripts/dm_conversation.py",
                   "set-icp-precheck", "--dm-id", args.dm_id, "--project", proj,
                   "--label", "unknown", "--notes", args.icp_note])

if __name__ == "__main__":
    main()
