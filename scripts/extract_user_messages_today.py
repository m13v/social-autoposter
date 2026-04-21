#!/usr/bin/env python3
"""Extract all user-authored messages sent on today's date across every Claude Code
session for the social-autoposter workspace.

Output: a single Markdown file grouped by session, with timestamps + content.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE_PROJECT_DIR = Path(
    "/Users/matthewdi/.claude/projects/-Users-matthewdi-social-autoposter"
)
TODAY_UTC = "2026-04-21"
OUT_PATH = Path(
    "/Users/matthewdi/social-autoposter/scripts/claude_user_messages_2026-04-21.md"
)
OUT_PATH_TRIMMED = Path(
    "/Users/matthewdi/social-autoposter/scripts/claude_user_messages_2026-04-21.interactive.md"
)


def iter_session_files():
    for p in sorted(WORKSPACE_PROJECT_DIR.glob("*.jsonl")):
        yield p


def content_to_text(content) -> str | None:
    """Return the textual user input for a 'user' entry, or None if this is a
    tool_result / non-user payload that should be skipped."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # A list is either a tool_result block (skip) or a list of text blocks.
        texts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_result":
                return None  # this is a tool response, not a user message
            if btype == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
        if texts:
            return "\n".join(texts)
    return None


def classify(text: str) -> str:
    """Tag a user-role message by origin:
    HUMAN       - you typed it in the terminal
    COMMAND     - slash-command invocation (<command-name>...)
    TASK_NOTIF  - background Task tool wake-up (<task-notification>)
    CMD_STDOUT  - output of a ! bang-command echoed back (<local-command-stdout>)
    SCHED_WAKE  - autonomous loop / scheduled wake-up sentinel
    SYS_REMIND  - pure <system-reminder> block injected by the harness
    HOOK        - user-prompt-submit-hook injection
    CLI_PROMPT  - non-interactive `claude -p "..."` feed (very long, no wrapper tags)
                  — same authorship as HUMAN (you or a script you own)
    """
    stripped = text.lstrip()
    if stripped.startswith("<task-notification>"):
        return "TASK_NOTIF"
    if stripped.startswith("<command-name>") or stripped.startswith("<command-message>"):
        return "COMMAND"
    if stripped.startswith("<local-command-stdout>") or stripped.startswith("<local-command-stderr>"):
        return "CMD_STDOUT"
    if "<<autonomous-loop" in stripped or stripped.startswith("<loop-"):
        return "SCHED_WAKE"
    if stripped.startswith("<user-prompt-submit-hook>"):
        return "HOOK"
    # A message that is ONLY system-reminder blocks (nothing else) is harness-injected.
    if stripped.startswith("<system-reminder>"):
        # strip all <system-reminder>...</system-reminder> blocks; if nothing left, it's pure sys
        import re
        without = re.sub(r"<system-reminder>.*?</system-reminder>", "", stripped, flags=re.DOTALL).strip()
        if not without:
            return "SYS_REMIND"
    return "HUMAN"


def extract_from_file(path: Path):
    msgs = []
    session_meta = {
        "session_id": path.stem,
        "path": str(path),
        "cwd": None,
        "first_ts": None,
        "last_ts": None,
    }
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = d.get("timestamp")
                if ts and session_meta["first_ts"] is None:
                    session_meta["first_ts"] = ts
                if ts:
                    session_meta["last_ts"] = ts

                if session_meta["cwd"] is None:
                    cwd = d.get("cwd")
                    if cwd:
                        session_meta["cwd"] = cwd

                if d.get("type") != "user":
                    continue
                msg = d.get("message") or {}
                if msg.get("role") != "user":
                    continue
                if not ts or not ts.startswith(TODAY_UTC):
                    continue

                text = content_to_text(msg.get("content"))
                if text is None:
                    continue
                text = text.strip()
                if not text:
                    continue

                msgs.append(
                    {
                        "timestamp": ts,
                        "promptId": d.get("promptId"),
                        "parentUuid": d.get("parentUuid"),
                        "isSidechain": d.get("isSidechain", False),
                        "kind": classify(text),
                        "text": text,
                    }
                )
    except OSError:
        return session_meta, []

    return session_meta, msgs


def main():
    sessions = []
    total_msgs = 0
    for path in iter_session_files():
        meta, msgs = extract_from_file(path)
        if not msgs:
            continue
        msgs.sort(key=lambda m: m["timestamp"])
        sessions.append((meta, msgs))
        total_msgs += len(msgs)

    # sort sessions by earliest message
    sessions.sort(key=lambda s: s[1][0]["timestamp"])

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # tally kinds
    kind_counts: dict[str, int] = {}
    for _meta, msgs in sessions:
        for m in msgs:
            kind_counts[m["kind"]] = kind_counts.get(m["kind"], 0) + 1

    lines: list[str] = []
    lines.append(f"# User messages for {TODAY_UTC}")
    lines.append("")
    lines.append(f"- Workspace: `/Users/matthewdi/social-autoposter`")
    lines.append(f"- Sessions with activity today: **{len(sessions)}**")
    lines.append(f"- Total user-role messages (excluding tool results): **{total_msgs}**")
    for k in ("HUMAN", "COMMAND", "TASK_NOTIF", "CMD_STDOUT", "SCHED_WAKE", "SYS_REMIND", "HOOK"):
        if k in kind_counts:
            lines.append(f"  - `{k}`: {kind_counts[k]}")
    lines.append(f"- Generated: {generated_at}")
    lines.append("")
    lines.append("Each section below is one Claude session. Messages are in chronological order.")
    lines.append("Tool-result blocks (role=user but produced by the harness) are excluded.")
    lines.append("")
    lines.append("Message **kind** tags:")
    lines.append("- `HUMAN` — typed by you (or fed non-interactively as the top-level prompt to `claude -p`)")
    lines.append("- `COMMAND` — slash-command invocation wrapper (`<command-name>...`)")
    lines.append("- `TASK_NOTIF` — background Task tool wake-up event")
    lines.append("- `CMD_STDOUT` — bang-command stdout/stderr echoed back into the conversation")
    lines.append("- `SCHED_WAKE` — autonomous loop or scheduled wake-up sentinel")
    lines.append("- `SYS_REMIND` — pure `<system-reminder>` injection (harness, not you)")
    lines.append("- `HOOK` — user-prompt-submit-hook injection")
    lines.append("")
    lines.append("To see only what you actually typed: grep for `kind=HUMAN` in this file.")
    lines.append("")
    lines.append("---")
    lines.append("")

    for idx, (meta, msgs) in enumerate(sessions, 1):
        lines.append(f"## Session {idx}: `{meta['session_id']}`")
        lines.append("")
        lines.append(f"- File: `{meta['path']}`")
        if meta.get("cwd"):
            lines.append(f"- cwd: `{meta['cwd']}`")
        lines.append(f"- First entry: `{meta['first_ts']}`")
        lines.append(f"- Last entry: `{meta['last_ts']}`")
        lines.append(f"- User-role messages today: **{len(msgs)}**")
        kc: dict[str, int] = {}
        for m in msgs:
            kc[m["kind"]] = kc.get(m["kind"], 0) + 1
        lines.append(f"- Breakdown: {', '.join(f'{k}={v}' for k, v in sorted(kc.items()))}")
        lines.append("")

        for i, m in enumerate(msgs, 1):
            sc = " (sidechain)" if m.get("isSidechain") else ""
            lines.append(f"### [{i}] {m['timestamp']} — kind={m['kind']}{sc}")
            if m.get("promptId"):
                lines.append(f"`promptId={m['promptId']}`")
            lines.append("")
            lines.append("```")
            lines.append(m["text"])
            lines.append("```")
            lines.append("")

        lines.append("---")
        lines.append("")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")

    print(f"wrote {OUT_PATH}")
    print(f"sessions: {len(sessions)}")
    print(f"user messages: {total_msgs}")

    # --- Trimmed output: interactive human messages only ---
    # Heuristic: a session is "interactive" if it contains >=2 distinct HUMAN promptIds.
    # Sessions with a single HUMAN promptId are almost always `claude -p` script fires
    # from skill/run-*.sh. Inside kept sessions we still drop non-HUMAN kinds.
    interactive_sessions = []
    trimmed_total = 0
    for meta, msgs in sessions:
        human_prompt_ids = {m["promptId"] for m in msgs if m["kind"] == "HUMAN" and m.get("promptId")}
        if len(human_prompt_ids) < 2:
            continue
        human_only = [m for m in msgs if m["kind"] == "HUMAN"]
        if not human_only:
            continue
        interactive_sessions.append((meta, human_only))
        trimmed_total += len(human_only)

    tl: list[str] = []
    tl.append(f"# Interactive human messages for {TODAY_UTC}")
    tl.append("")
    tl.append(f"- Workspace: `/Users/matthewdi/social-autoposter`")
    tl.append(f"- Filter: sessions with ≥2 distinct HUMAN `promptId`s (proxy for interactive)")
    tl.append(f"- Kept sessions: **{len(interactive_sessions)}** / {len(sessions)}")
    tl.append(f"- Kept messages: **{trimmed_total}** / {total_msgs}")
    tl.append(f"- Generated: {generated_at}")
    tl.append("")
    tl.append("Note: `claude -p` non-interactive script invocations (skill/run-*.sh) fire a single")
    tl.append("templated prompt per session, so they have exactly one HUMAN promptId and are")
    tl.append("filtered out here. Any session that kept you in a back-and-forth conversation")
    tl.append("survived the filter.")
    tl.append("")
    tl.append("---")
    tl.append("")
    for idx, (meta, msgs) in enumerate(interactive_sessions, 1):
        tl.append(f"## Session {idx}: `{meta['session_id']}`")
        tl.append("")
        if meta.get("cwd"):
            tl.append(f"- cwd: `{meta['cwd']}`")
        tl.append(f"- First entry: `{meta['first_ts']}`")
        tl.append(f"- Last entry: `{meta['last_ts']}`")
        tl.append(f"- HUMAN messages today: **{len(msgs)}**")
        tl.append("")
        for i, m in enumerate(msgs, 1):
            tl.append(f"### [{i}] {m['timestamp']}")
            tl.append("")
            tl.append("```")
            tl.append(m["text"])
            tl.append("```")
            tl.append("")
        tl.append("---")
        tl.append("")

    OUT_PATH_TRIMMED.write_text("\n".join(tl), encoding="utf-8")
    print(f"wrote {OUT_PATH_TRIMMED}")
    print(f"interactive sessions: {len(interactive_sessions)}")
    print(f"interactive HUMAN messages: {trimmed_total}")


if __name__ == "__main__":
    main()
