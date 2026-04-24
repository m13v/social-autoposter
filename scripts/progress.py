#!/usr/bin/env python3
"""Tiny progress heartbeat writer for long-running stats jobs.

Each call atomically replaces skill/cache/progress_<platform>.json with a
snapshot of where the job is. Readers (dashboard, CLI status check, humans)
can cat the file at any time - or if the job dies mid-run, the last heartbeat
survives so we know how far it got before being killed (watchdog, Claude
rate limit, OS OOM, etc.).

Writes are best-effort: any failure here is swallowed so a broken disk or
permission issue never breaks the stats job itself.

CLI usage:
    python3 scripts/progress.py           # show all current heartbeats
    python3 scripts/progress.py github    # show only github
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / "skill" / "cache"


def tick(platform, done, total, **extras):
    """Write a heartbeat showing `done`/`total` for `platform`.

    Extra fields (updated, errors, deleted, state, etc.) are merged in.
    """
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = CACHE_DIR / f"progress_{platform}.json"
        now = time.time()
        payload = {
            "platform": platform,
            "done": done,
            "total": total,
            "pid": os.getpid(),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
            "updated_at_ts": int(now),
            **extras,
        }
        fd, tmp = tempfile.mkstemp(prefix=f".progress_{platform}_",
                                   suffix=".json", dir=str(CACHE_DIR))
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
    except Exception:
        pass


def done(platform, total, **extras):
    """Mark `platform` as completed. Final tick, done==total, state=done."""
    tick(platform, total, total, state="done", **extras)


def _show(platform=None):
    if not CACHE_DIR.exists():
        return
    files = sorted(CACHE_DIR.glob("progress_*.json"))
    for f in files:
        name = f.stem.replace("progress_", "")
        if platform and name != platform:
            continue
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        age = int(time.time() - data.get("updated_at_ts", 0))
        state = data.get("state", "running")
        done_n = data.get("done", 0)
        total_n = data.get("total", 0)
        pct = f"{100 * done_n / total_n:.1f}%" if total_n else "?"
        print(f"{name:10} {state:8} {done_n}/{total_n} ({pct})  pid={data.get('pid')}  {age}s ago  @ {data.get('updated_at')}")


if __name__ == "__main__":
    _show(sys.argv[1] if len(sys.argv) > 1 else None)
