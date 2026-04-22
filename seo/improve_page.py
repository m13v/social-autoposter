#!/usr/bin/env python3
"""Hand a picked-page brief to a Claude session running inside the target
repo and let it experiment with the page content.

Intentionally open-minded. The prompt does not restrict the model to SEO
pages, to specific component libraries, or to a fixed list of editable
files. Claude is told what the page is, how it is performing, where to
find it in the repo, and what the product positioning looks like, and is
free to WebSearch for fresh landing-page patterns, trending copy angles,
or conversion ideas, then Read/Edit whatever it decides improves the page.

Lifecycle for each invocation:

  1. Record a 'running' row in seo_page_improvements with the brief snapshot.
  2. Launch `claude -p ...` with cwd set to the product repo, capturing
     stream-json events for auditability and cost tracking.
  3. Require a final JSON envelope on stdout summarising the change.
  4. Diff HEAD before/after to confirm a commit actually landed; write
     commit_sha, files_modified, diff_summary, rationale back to the row
     and flip status to 'committed' / 'no_change' / 'failed'.

Usage:
    python3 seo/improve_page.py --brief /tmp/brief_pieline.json
    python3 seo/improve_page.py --brief /tmp/brief_cyrano.json --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent

ENV_PATH = ROOT_DIR / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import psycopg2  # noqa: E402
from psycopg2.extras import Json  # noqa: E402

sys.path.insert(0, str(SCRIPT_DIR))
from claude_wait import wait_for_claude  # noqa: E402


CLAUDE_TIMEOUT_SECONDS = 1800  # 30 minutes; research + multiple edits + commit


PROMPT_TEMPLATE = """You are improving a live landing page that real visitors hit right now. You have full read/write access to the website repo (cwd = repo root). Your job is to make the page convert better.

# Context

{brief_block}

# What counts as success

The page above is the most-visited page on this site over the last 24 hours. Your goal is to increase useful conversions on it over the next 24 hours:

- more pageviews (via on-page SEO, internal links, or better shareable copy)
- more email signups (stronger lead capture, better incentive, better placement)
- more schedule-demo clicks (for products with booking)
- more get-started / signup clicks (for products with downloads/installs)
- more real bookings where applicable

You have one run. Pick the change you believe has the highest expected impact and ship it.

# How to work

1. Start by READING the live page file(s). Find them in the repo. Most products put the home page at `src/app/page.tsx` or `src/app/(main)/page.tsx`; other paths map to `src/app/<path>/page.tsx` or similar. Use Glob/Grep liberally.
2. Do fresh web research. Use WebSearch for:
   - what landing pages for this specific product category are doing well right now
   - what headline/hero/CTA patterns are trending for this buyer
   - any new positioning angles, objections, or proof points worth surfacing
   - competitor pages that currently rank for this page's intent
   Pull at least two distinct external sources, not just one. Do not copy verbatim; synthesize.
3. Decide on ONE substantive change set. Examples (not an exhaustive list, be creative):
   - rewrite the hero headline / subhead / primary CTA
   - add a new section (proof, social proof, comparison, FAQ, before/after, live demo, metrics strip)
   - re-order sections so the strongest argument lands first
   - replace weak proof with stronger proof from project_config
   - tighten dense paragraphs, replace walls of text with scannable structure
   - add internal links to related pages that will boost SEO clustering
   - improve meta title/description for organic CTR
4. You are NOT restricted to any particular component library. The repo likely uses `@seo/components` / `@m13v/seo-components` plus local components under `src/components/`. Reuse those when they fit, build inline TSX/JSX when they don't.
5. Edit the files. Keep changes focused and high-signal; if you find yourself changing 10 files you are probably refactoring, stop.
6. Run the repo's typecheck / build if one exists under `package.json` scripts and it is cheap. If a quick check fails due to your edit, fix it before committing.
7. Stage and commit ALL your changes with a single commit:

   ```
   git add -A
   git commit -m "improve: {commit_subject}" -m "<one short paragraph of rationale>"
   ```

   Do NOT push. Do NOT amend prior commits. Do NOT run `git reset --hard`, `git checkout`, or delete branches. The repo's auto-commit agent handles pushing.

# Output

After committing, end your final assistant message with EXACTLY one fenced JSON block, nothing after it:

```json
{{
  "status": "committed" | "no_change" | "failed",
  "files_modified": ["path/relative/to/repo", ...],
  "diff_summary": "one-paragraph plain-English summary of what changed and why",
  "rationale": "the single most important reason you expect this to lift conversions",
  "web_sources_used": ["url1", "url2", ...],
  "commit_sha": "<short sha or empty>",
  "notes_for_next_run": "anything you want the next 24h run to know (tests ran, hypotheses to validate, etc.)"
}}
```

If you genuinely cannot improve the page (e.g. it is already excellent and any change would be noise), set status="no_change" and explain in rationale. Do not ship busywork.
"""


def _render_brief_block(brief: dict) -> str:
    """Render the brief as a compact structured block Claude can skim.

    We pass the full project_config as pretty JSON at the bottom so the model
    has the source of truth (voice, positioning, qualification, proof points,
    pricing) without us having to re-synthesize it.
    """
    m = brief.get("metrics") or {}
    m24 = m.get("24h") or {}
    m7 = m.get("7d_avg_per_day") or {}
    m30 = m.get("30d_avg_per_day") or {}

    def _fmt_metric(row):
        if not row:
            return "n/a"
        parts = []
        for k in ("pageviews", "email_signups", "schedule_clicks", "get_started_clicks", "bookings"):
            v = row.get(k)
            parts.append(f"{k}={v}")
        return ", ".join(parts)

    hist_rows = brief.get("history") or []
    if hist_rows:
        hist = "\n".join(
            f"  - {h.get('at','?')} [{h.get('status','?')}] "
            f"{(h.get('diff_summary') or '').strip()[:180]}"
            for h in hist_rows
        )
    else:
        hist = "  (none; this page has not been touched by this pipeline before)"

    cfg_json = json.dumps(brief.get("project_config") or {}, indent=2, ensure_ascii=False)
    return (
        f"Product: {brief.get('product')}\n"
        f"Domain: {brief.get('domain')}\n"
        f"Page path: {brief.get('page_path')}\n"
        f"Live URL: {brief.get('page_url')}\n"
        f"Repo (cwd): {brief.get('repo_path')}\n"
        f"\n"
        f"Traffic + funnel:\n"
        f"  last 24h       : {_fmt_metric(m24)}\n"
        f"  7d avg / day   : {_fmt_metric(m7)}  (totals={json.dumps(m7.get('totals') or {})})\n"
        f"  30d avg / day  : {_fmt_metric(m30)}  (totals={json.dumps(m30.get('totals') or {})})\n"
        f"\n"
        f"Prior improvement runs on THIS page:\n{hist}\n"
        f"\n"
        f"Full product config (authoritative source for voice, positioning, qualification, proof, pricing):\n"
        f"```json\n{cfg_json}\n```\n"
    )


def _git_head_sha(repo_path: str) -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=15,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _git_changed_files(repo_path: str, base_sha: str) -> list[str]:
    if not base_sha:
        return []
    try:
        r = subprocess.run(
            ["git", "diff", "--name-only", base_sha, "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            return []
        return [line.strip() for line in r.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def _extract_final_json(text: str) -> dict:
    """Pull the last fenced ```json ... ``` block or last {...} block."""
    if not text:
        return {}
    # prefer fenced
    fence = "```json"
    idx = text.rfind(fence)
    if idx != -1:
        rest = text[idx + len(fence):]
        end = rest.find("```")
        if end != -1:
            body = rest[:end].strip()
            try:
                return json.loads(body)
            except Exception:
                pass
    # fall back to last { ... } balanced block
    last_close = text.rfind("}")
    if last_close != -1:
        depth = 0
        for i in range(last_close, -1, -1):
            ch = text[i]
            if ch == "}":
                depth += 1
            elif ch == "{":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[i:last_close + 1])
                    except Exception:
                        break
    return {}


def _run_claude(prompt: str, cwd: str, log_path: Path, session_id: str) -> dict:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "claude", "-p", prompt,
        "--session-id", session_id,
        "--output-format", "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
    ]
    tool_counts: dict[str, int] = {}
    final_text = ""
    start = time.time()

    # Bridge the Claude Code auto-update unlink window before spawning.
    if not wait_for_claude():
        return {"exit_code": 127, "final_text": "", "tool_counts": {},
                "error": "claude CLI not on PATH after wait_for_claude timeout"}

    with open(log_path, "w") as log_f:
        try:
            proc = subprocess.Popen(
                cmd, cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
            )
        except FileNotFoundError:
            return {"exit_code": 127, "final_text": "", "tool_counts": {},
                    "error": "claude CLI not on PATH"}

        assert proc.stdout is not None
        while True:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            log_f.write(line); log_f.flush()
            if time.time() - start > CLAUDE_TIMEOUT_SECONDS:
                proc.kill()
                return {"exit_code": 124, "final_text": final_text,
                        "tool_counts": tool_counts,
                        "error": f"timeout after {CLAUDE_TIMEOUT_SECONDS}s"}
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "assistant":
                for block in (ev.get("message") or {}).get("content") or []:
                    if block.get("type") == "tool_use":
                        name = block.get("name") or "unknown"
                        tool_counts[name] = tool_counts.get(name, 0) + 1
            elif ev.get("type") == "result":
                final_text = ev.get("result") or ""
        proc.wait()

    # fire-and-forget cost logging so runs show up in claude_sessions
    logger = ROOT_DIR / "scripts" / "log_claude_session.py"
    if logger.exists():
        try:
            subprocess.run(
                ["python3", str(logger), "--session-id", session_id, "--script", "seo_improve_page"],
                capture_output=True, text=True, timeout=30,
            )
        except Exception:
            pass

    return {
        "exit_code": proc.returncode,
        "final_text": final_text,
        "tool_counts": tool_counts,
    }


def _insert_running_row(brief: dict) -> int:
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO seo_page_improvements "
        "(product, domain, page_path, page_url, metrics_24h, metrics_7d_avg, metrics_30d_avg, "
        " brief_json, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'running') RETURNING id",
        (
            brief["product"], brief["domain"], brief["page_path"], brief["page_url"],
            Json(brief["metrics"]["24h"]),
            Json(brief["metrics"]["7d_avg_per_day"]),
            Json(brief["metrics"]["30d_avg_per_day"]),
            Json(brief),
        ),
    )
    row_id = cur.fetchone()[0]
    conn.commit(); cur.close(); conn.close()
    return row_id


def _finish_row(row_id: int, **fields):
    if not fields:
        return
    cols = []
    vals = []
    for k, v in fields.items():
        cols.append(f"{k} = %s")
        if isinstance(v, (dict, list)) and k in ("tool_summary",):
            vals.append(Json(v))
        else:
            vals.append(v)
    cols.append("completed_at = NOW()")
    vals.append(row_id)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()
    cur.execute(f"UPDATE seo_page_improvements SET {', '.join(cols)} WHERE id = %s", vals)
    conn.commit(); cur.close(); conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", required=True, help="Path to brief JSON from pick_top_page.py")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the rendered prompt and exit; do not launch Claude or write to DB")
    args = ap.parse_args()

    brief = json.loads(Path(args.brief).read_text())
    repo_path = brief["repo_path"]
    if not os.path.isdir(repo_path):
        raise SystemExit(f"ERROR: repo_path does not exist: {repo_path}")

    commit_subject = f"{brief['page_path']} (top-traffic improve run)"
    prompt = PROMPT_TEMPLATE.format(
        brief_block=_render_brief_block(brief),
        commit_subject=commit_subject,
    )

    if args.dry_run:
        sys.stdout.write(prompt)
        return

    session_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_slug = (brief["page_path"] or "root").strip("/").replace("/", "_") or "root"
    log_dir = SCRIPT_DIR / "logs" / brief["product"].lower() / "improve"
    log_path = log_dir / f"{ts}_{safe_slug}_stream.jsonl"

    row_id = _insert_running_row(brief)
    base_sha = _git_head_sha(repo_path)

    result = _run_claude(prompt=prompt, cwd=repo_path, log_path=log_path, session_id=session_id)
    head_sha = _git_head_sha(repo_path)
    changed_files = _git_changed_files(repo_path, base_sha) if base_sha else []

    final_json = _extract_final_json(result.get("final_text") or "")
    model_status = (final_json.get("status") or "").lower() if final_json else ""

    # Ground truth: did the commit actually move?
    committed = bool(base_sha and head_sha and head_sha != base_sha)
    if result.get("exit_code") not in (0,):
        status = "failed"
    elif committed:
        status = "committed"
    elif model_status == "no_change":
        status = "no_change"
    else:
        # Model claimed success but HEAD didn't move. Flag as no_change — auto
        # commit agent may still pick up uncommitted changes below, but that's
        # a separate story.
        status = "no_change"

    err = result.get("error") or None

    _finish_row(
        row_id,
        claude_session_id=session_id,
        run_log_path=str(log_path),
        tool_summary=result.get("tool_counts") or {},
        final_result_text=(result.get("final_text") or "")[:20000],
        commit_sha=head_sha if committed else None,
        files_modified=changed_files or (final_json.get("files_modified") or []),
        diff_summary=(final_json.get("diff_summary") or "")[:4000] if final_json else None,
        rationale=(final_json.get("rationale") or "")[:4000] if final_json else None,
        status=status,
        error=err,
    )

    print(json.dumps({
        "row_id": row_id,
        "session_id": session_id,
        "status": status,
        "exit_code": result.get("exit_code"),
        "commit_sha": head_sha if committed else None,
        "files_modified": changed_files,
        "log_path": str(log_path),
    }, indent=2))


if __name__ == "__main__":
    main()
