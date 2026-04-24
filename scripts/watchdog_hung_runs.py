#!/usr/bin/env python3
"""Kill launchd-parented skill/*.sh processes that have been running too long.

Matches the hang pattern flagged in CLAUDE.md: a run-*.sh spawns `claude -p`
which blocks indefinitely (e.g. BSD grep on stale /tmp FIFOs), preventing
launchd from re-firing the job on its StartInterval.

For every kill, emits a synthetic log_run.py entry so the stuck run surfaces
as a failed job in the dashboard's job-history table (run_monitor.log), and
appends a line to skill/logs/watchdog.log for the kill trail.
"""

import os
import subprocess
import time
from pathlib import Path

REPO = Path("/Users/matthewdi/social-autoposter")
LOG_RUN_PY = REPO / "scripts" / "log_run.py"
SKILL_PATH_MARKER = "/social-autoposter/skill/"
MAX_AGE_SEC = 45 * 60
WATCHDOG_LOG = REPO / "skill" / "logs" / "watchdog.log"

# Map skill/*.sh filename -> script label used by the script's own log_run.py
# calls. Keeps dashboard job-history grouping consistent (e.g. a killed
# run-twitter-cycle.sh shows under the same "Post · Twitter" row as a normal
# post_twitter run). Unknown scripts fall through to a watchdog_killed_* label.
SCRIPT_LABELS = {
    "run-twitter-cycle.sh": "post_twitter",
    "run-linkedin.sh": "post_linkedin",
    "run-moltbook.sh": "post_moltbook",
    "run-reddit-threads.sh": "post_reddit",
    "run-reddit-search.sh": "post_reddit",
    "run-github.sh": "post_github",
    "run-scan-moltbook-replies.sh": "scan_moltbook_replies",
    "run-scan-reddit-replies.sh": "scan_reddit_replies",
    "scan-twitter-followups.sh": "scan_twitter_followups",
    "engage-twitter.sh": "engage_twitter",
    "engage-linkedin.sh": "engage_linkedin",
    "engage-moltbook.sh": "engage_moltbook",
    "engage.sh": "engage_reddit",
    "github-engage.sh": "engage_github",
    "engage-dm-replies-twitter.sh": "dm_replies_twitter",
    "engage-dm-replies-linkedin.sh": "dm_replies_linkedin",
    "engage-dm-replies-reddit.sh": "dm_replies_reddit",
    "engage-dm-replies.sh": "dm_replies_reddit",
    "dm-outreach-twitter.sh": "dm_outreach_twitter",
    "dm-outreach-linkedin.sh": "dm_outreach_linkedin",
    "dm-outreach-reddit.sh": "dm_outreach_reddit",
    "link-edit-twitter.sh": "link_edit_twitter",
    "link-edit-linkedin.sh": "link_edit_linkedin",
    "link-edit-moltbook.sh": "link_edit_moltbook",
    "link-edit-reddit.sh": "link_edit_reddit",
    "link-edit-github.sh": "link_edit_github",
    "audit-twitter.sh": "audit-twitter",
    "audit-linkedin.sh": "audit-linkedin",
    "audit-moltbook.sh": "audit-moltbook",
    "audit-reddit.sh": "audit-reddit",
    "audit-reddit-resurrect.sh": "audit-reddit-resurrect",
    "audit-dm-staleness.sh": "audit-dm-staleness",
    "octolens-twitter.sh": "octolens-twitter",
    "octolens-linkedin.sh": "octolens-linkedin",
    "octolens-reddit.sh": "octolens-reddit",
    "stats-twitter.sh": "stats_twitter",
    "stats-linkedin.sh": "stats_linkedin",
    "stats-moltbook.sh": "stats_moltbook",
    "stats-reddit.sh": "stats_reddit",
}


def watchdog_log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    line = f"{ts} | {msg}\n"
    WATCHDOG_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(WATCHDOG_LOG, "a") as f:
        f.write(line)
    print(line, end="")


def list_skill_shell_processes():
    """Return [(pid, ppid, etimes_sec, script_filename)] for skill/*.sh bash procs."""
    res = subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid=,etime=,command="],
        capture_output=True, text=True, check=True,
    )
    procs = []
    for raw in res.stdout.splitlines():
        parts = raw.strip().split(None, 3)
        if len(parts) < 4:
            continue
        pid_s, ppid_s, etime_s, command = parts
        try:
            pid = int(pid_s)
            ppid = int(ppid_s)
        except ValueError:
            continue
        if SKILL_PATH_MARKER not in command:
            continue
        script_name = None
        for tok in command.split():
            if tok.endswith(".sh") and SKILL_PATH_MARKER in tok:
                script_name = os.path.basename(tok)
                break
        if not script_name:
            continue
        etimes = _parse_etime(etime_s)
        if etimes is None:
            continue
        procs.append((pid, ppid, etimes, script_name))
    return procs


def _parse_etime(s: str):
    """Parse ps etime format ([[DD-]HH:]MM:SS) into seconds."""
    try:
        days = 0
        if "-" in s:
            d, s = s.split("-", 1)
            days = int(d)
        parts = s.split(":")
        parts = [int(p) for p in parts]
        if len(parts) == 2:
            h, m, sec = 0, parts[0], parts[1]
        elif len(parts) == 3:
            h, m, sec = parts
        else:
            return None
        return days * 86400 + h * 3600 + m * 60 + sec
    except Exception:
        return None


def descendants(pid: int):
    out = [pid]
    i = 0
    while i < len(out):
        try:
            r = subprocess.run(
                ["pgrep", "-P", str(out[i])],
                capture_output=True, text=True,
            )
            for tok in r.stdout.split():
                if tok.isdigit():
                    out.append(int(tok))
        except Exception:
            pass
        i += 1
    return out


def kill_tree(root_pid: int) -> list:
    pids = descendants(root_pid)
    for p in reversed(pids):
        try:
            os.kill(p, 15)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
    time.sleep(2)
    for p in reversed(pids):
        try:
            os.kill(p, 9)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass
    return pids


def emit_job_log(script_file: str, elapsed_sec: int) -> None:
    label = SCRIPT_LABELS.get(
        script_file,
        "watchdog_killed_" + script_file.replace(".sh", "").replace("-", "_"),
    )
    subprocess.run(
        [
            "python3", str(LOG_RUN_PY),
            "--script", label,
            "--posted", "0",
            "--skipped", "0",
            "--failed", "1",
            "--cost", "0",
            "--elapsed", str(elapsed_sec),
        ],
        check=False,
    )


def main() -> None:
    procs = list_skill_shell_processes()
    for pid, ppid, etimes, script_file in procs:
        if ppid != 1:
            continue
        if etimes < MAX_AGE_SEC:
            continue
        watchdog_log(
            f"KILL {script_file} pid={pid} elapsed={etimes}s cap={MAX_AGE_SEC}s"
        )
        killed = kill_tree(pid)
        watchdog_log(f"  killed pids: {killed}")
        emit_job_log(script_file, etimes)


if __name__ == "__main__":
    main()
