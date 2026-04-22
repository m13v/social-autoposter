"""
Shared precheck for subprocess calls that spawn `claude`.

Claude Code auto-update (`npm install -g @anthropic-ai/claude-code`)
briefly unlinks ~/.nvm/versions/node/<ver>/bin/claude between old and new
symlink. A subprocess.Popen(['claude', ...]) during that window raises
FileNotFoundError. Calling wait_for_claude() before Popen() bridges the gap.

Once Popen succeeds the running process is safe: npm rewriting the
symlink does not kill an already-exec'd binary.
"""

from __future__ import annotations

import shutil
import sys
import time


def wait_for_claude(max_wait: float = 120.0, check_interval: float = 5.0) -> bool:
    start = time.time()
    logged = False
    while shutil.which("claude") is None:
        if time.time() - start >= max_wait:
            print(
                f"  claude_wait: binary missing after {max_wait:.0f}s",
                file=sys.stderr,
                flush=True,
            )
            return False
        if not logged:
            print(
                "  claude_wait: binary missing, waiting (auto-update?)",
                file=sys.stderr,
                flush=True,
            )
            logged = True
        time.sleep(check_interval)
    if logged:
        elapsed = time.time() - start
        print(
            f"  claude_wait: binary appeared after {elapsed:.0f}s",
            file=sys.stderr,
            flush=True,
        )
    return True
