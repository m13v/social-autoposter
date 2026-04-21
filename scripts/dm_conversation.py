#!/usr/bin/env python3
"""DM conversation tracker - log messages, query history, update state.

This is the central module for all DM conversation tracking. Every DM
interaction (outbound or inbound) should go through here.

Usage:
    # Log an outbound message we sent
    python3 scripts/dm_conversation.py log-outbound --dm-id 5 --content "hey, what stack..."

    # Log an inbound message we received
    python3 scripts/dm_conversation.py log-inbound --dm-id 5 --author tolley --content "I use React..."

    # Show full conversation history for a DM
    python3 scripts/dm_conversation.py history --dm-id 5

    # Show all conversations with pending inbound (needs reply)
    python3 scripts/dm_conversation.py pending

    # Set chat URL for a conversation
    python3 scripts/dm_conversation.py set-url --dm-id 5 --url "https://www.reddit.com/chat/room/..."

    # Update conversation tier
    python3 scripts/dm_conversation.py set-tier --dm-id 5 --tier 2

    # Mark conversation status
    python3 scripts/dm_conversation.py set-status --dm-id 5 --status converted

    # Find DM by author name (fuzzy)
    python3 scripts/dm_conversation.py find --author tolley

    # Summary of all active conversations
    python3 scripts/dm_conversation.py summary
"""

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime, timezone
from email.mime.text import MIMEText

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

CONFIG_PATH = os.path.expanduser("~/social-autoposter/config.json")
GMAIL_TOKEN_PATH = os.path.expanduser("~/gmail-api/token_i_at_m13v.com.json")
GMAIL_SCOPES = ["https://mail.google.com/"]


def _valid_chat_url(platform, url):
    """Return a cleaned chat_url or None.

    The dashboard only treats it as an "open chat" link when it looks like a
    real DM thread URL. Post URLs / profile URLs silently leak in when the
    prompt passes the wrong variable, so we reject anything that is not the
    per-platform DM-thread shape.
    """
    if not url:
        return None
    u = url.strip()
    if not u:
        return None
    p = (platform or "").lower()
    if p == "reddit":
        if "/chat/room/" in u or "/message/messages/" in u:
            return u
        if "/room/!" in u and "/chat/room/!" not in u:
            return u.replace("/room/!", "/chat/room/!", 1)
        return None
    if p in ("twitter", "x"):
        if "/i/chat/" in u or "/messages/" in u:
            return u
        return None
    if p == "linkedin":
        if "/messaging/thread/" in u:
            return u
        return None
    return u


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def get_our_account(config, platform):
    accounts = config.get("accounts", {})
    if platform == "reddit":
        return accounts.get("reddit", {}).get("username", "Deep_Ad1959")
    elif platform == "linkedin":
        return accounts.get("linkedin", {}).get("name", "Matthew Diakonov")
    elif platform == "x":
        return accounts.get("twitter", {}).get("handle", "@m13v_").lstrip("@")
    return "unknown"


def log_outbound(conn, dm_id, content, author=None):
    """Log a message we sent. Includes dedup guard to prevent double-sending."""
    row = conn.execute("SELECT platform, their_author FROM dms WHERE id = %s", (dm_id,)).fetchone()
    if not row:
        print(f"ERROR: DM #{dm_id} not found")
        return False

    # Dedup guard: check if last message is already outbound (no inbound since our last reply)
    last_msg = conn.execute("""
        SELECT direction, content, message_at FROM dm_messages
        WHERE dm_id = %s ORDER BY message_at DESC LIMIT 1
    """, (dm_id,)).fetchone()

    if last_msg and last_msg["direction"] == "outbound":
        hours_ago = (datetime.now() - last_msg["message_at"]).total_seconds() / 3600
        print(f"  DEDUP BLOCKED: Last message to {row['their_author']} (DM #{dm_id}) was already outbound ({hours_ago:.1f}h ago). Skipping.")
        return False

    config = load_config()
    if not author:
        author = get_our_account(config, row["platform"])

    claude_session_id = os.environ.get("CLAUDE_SESSION_ID") or None

    conn.execute("""
        INSERT INTO dm_messages (dm_id, direction, author, content, message_at, logged_at, claude_session_id)
        VALUES (%s, 'outbound', %s, %s, NOW(), NOW(), %s)
    """, (dm_id, author, content, claude_session_id))

    conn.execute("""
        UPDATE dms SET last_message_at = NOW(), message_count = message_count + 1,
                       conversation_status = 'active',
                       claude_session_id = COALESCE(%s, claude_session_id)
        WHERE id = %s
    """, (claude_session_id, dm_id))
    conn.commit()
    print(f"  Logged outbound to {row['their_author']} (DM #{dm_id})")
    return True


def ensure_dm(conn, platform, author, chat_url=None, lookback_hours=720):
    """Return the dm_id for (platform, author), creating the row if missing.

    On insert, backfill reply_id + post_id from the most recent matching
    replies row so the dashboard (and any other join-based tooling) has
    the public-engagement chain linked. Without this, inbound-first DMs
    get stored with both FKs NULL and the operator has to fall back to
    the dashboard's fuzzy-match renderer. The lookback window prevents
    linking to an unrelated reply from months earlier, which would be
    misleading for a cold DM that happens to share an author name with
    some ancient thread engagement.
    """
    clean_chat_url = _valid_chat_url(platform, chat_url)
    if chat_url and not clean_chat_url:
        print(f"  WARN: ignoring non-DM chat_url for {platform}/@{author}: {chat_url[:120]}",
              file=sys.stderr)
    existing = conn.execute(
        "SELECT id FROM dms WHERE platform = %s AND their_author = %s ORDER BY id DESC LIMIT 1",
        (platform, author),
    ).fetchone()
    if existing:
        if clean_chat_url:
            conn.execute(
                "UPDATE dms SET chat_url = COALESCE(chat_url, %s) WHERE id = %s",
                (clean_chat_url, existing["id"]),
            )
            conn.commit()
        return existing["id"], False, None

    match = conn.execute(
        """
        SELECT id, post_id, their_content, their_comment_url
        FROM replies
        WHERE platform = %s AND their_author = %s
          AND discovered_at >= NOW() - (%s || ' hours')::interval
        ORDER BY discovered_at DESC
        LIMIT 1
        """,
        (platform, author, str(lookback_hours)),
    ).fetchone()

    reply_id = match["id"] if match else None
    post_id = match["post_id"] if match else None
    comment_ctx = None
    if match and match.get("their_content"):
        comment_ctx = match["their_content"][:1000]

    row = conn.execute(
        """
        INSERT INTO dms (platform, their_author, reply_id, post_id,
                         comment_context, chat_url, status, conversation_status,
                         tier, discovered_at)
        VALUES (%s, %s, %s, %s, %s, %s, 'sent', 'active', 1, NOW())
        RETURNING id
        """,
        (platform, author, reply_id, post_id, comment_ctx, clean_chat_url),
    ).fetchone()
    conn.commit()
    return row["id"], True, reply_id


def log_inbound(conn, dm_id, author, content, message_at=None):
    """Log a message we received."""
    row = conn.execute("SELECT platform, their_author FROM dms WHERE id = %s", (dm_id,)).fetchone()
    if not row:
        print(f"ERROR: DM #{dm_id} not found")
        return False

    ts = message_at or "NOW()"
    if message_at:
        conn.execute("""
            INSERT INTO dm_messages (dm_id, direction, author, content, message_at, logged_at)
            VALUES (%s, 'inbound', %s, %s, %s, NOW())
        """, (dm_id, author, content, message_at))
    else:
        conn.execute("""
            INSERT INTO dm_messages (dm_id, direction, author, content, message_at, logged_at)
            VALUES (%s, 'inbound', %s, %s, NOW(), NOW())
        """, (dm_id, author, content))

    conn.execute("""
        UPDATE dms SET last_message_at = NOW(), message_count = message_count + 1,
                       conversation_status = 'needs_reply'
        WHERE id = %s
    """, (dm_id,))
    conn.commit()
    print(f"  Logged inbound from {author} (DM #{dm_id})")
    return True


def show_history(conn, dm_id):
    """Print full conversation history."""
    dm = conn.execute("""
        SELECT d.id, d.platform, d.their_author, d.chat_url, d.conversation_status,
               d.tier, d.message_count, d.comment_context
        FROM dms d WHERE d.id = %s
    """, (dm_id,)).fetchone()

    if not dm:
        print(f"DM #{dm_id} not found")
        return

    print(f"=== DM #{dm['id']} with {dm['their_author']} [{dm['platform']}] ===")
    print(f"Status: {dm['conversation_status']}  Tier: {dm['tier']}  Messages: {dm['message_count']}")
    if dm['chat_url']:
        print(f"Chat URL: {dm['chat_url']}")
    if dm['comment_context']:
        print(f"Original context: {dm['comment_context'][:200]}...")
    print()

    messages = conn.execute("""
        SELECT direction, author, content, message_at
        FROM dm_messages WHERE dm_id = %s ORDER BY message_at ASC
    """, (dm_id,)).fetchall()

    for msg in messages:
        arrow = ">>" if msg["direction"] == "outbound" else "<<"
        ts = msg["message_at"].strftime("%Y-%m-%d %H:%M") if msg["message_at"] else "?"
        print(f"  {arrow} [{ts}] {msg['author']}: {msg['content']}")
    print()


def show_pending(conn):
    """Show conversations that have inbound messages we haven't replied to."""
    rows = conn.execute("""
        SELECT d.id, d.platform, d.their_author, d.chat_url, d.tier,
               d.message_count, d.last_message_at,
               (SELECT content FROM dm_messages
                WHERE dm_id = d.id ORDER BY message_at DESC LIMIT 1) as last_msg,
               (SELECT direction FROM dm_messages
                WHERE dm_id = d.id ORDER BY message_at DESC LIMIT 1) as last_dir
        FROM dms d
        WHERE d.conversation_status = 'needs_reply'
          OR (d.status = 'sent' AND d.conversation_status = 'active'
              AND EXISTS (
                SELECT 1 FROM dm_messages m
                WHERE m.dm_id = d.id AND m.direction = 'inbound'
                AND m.message_at > COALESCE(
                    (SELECT MAX(m2.message_at) FROM dm_messages m2
                     WHERE m2.dm_id = d.id AND m2.direction = 'outbound'), '1970-01-01')
              ))
        ORDER BY d.last_message_at DESC
    """).fetchall()

    if not rows:
        print("No conversations needing reply.")
        return

    print(f"=== {len(rows)} conversations need reply ===\n")
    for r in rows:
        tier_label = f"T{r['tier']}" if r['tier'] else "T1"
        ts = r['last_message_at'].strftime("%m/%d %H:%M") if r['last_message_at'] else "?"
        last = (r['last_msg'] or "")[:100]
        print(f"  DM #{r['id']} [{r['platform']}] {r['their_author']} ({tier_label}, {r['message_count']} msgs, last: {ts})")
        print(f"    Last: {last}")
        if r['chat_url']:
            print(f"    URL: {r['chat_url']}")
        print()


def find_by_author(conn, author_query):
    """Find DM conversations by author name (case-insensitive partial match)."""
    rows = conn.execute("""
        SELECT d.id, d.platform, d.their_author, d.status, d.conversation_status,
               d.tier, d.message_count, d.chat_url, d.last_message_at
        FROM dms d
        WHERE LOWER(d.their_author) LIKE LOWER(%s)
        ORDER BY d.last_message_at DESC NULLS LAST
    """, (f"%{author_query}%",)).fetchall()

    if not rows:
        print(f"No DMs found matching '{author_query}'")
        return

    for r in rows:
        ts = r['last_message_at'].strftime("%m/%d %H:%M") if r['last_message_at'] else "never"
        print(f"  DM #{r['id']} [{r['platform']}] {r['their_author']} - {r['status']}/{r['conversation_status']} T{r['tier'] or 1} ({r['message_count']} msgs, last: {ts})")
        if r['chat_url']:
            print(f"    URL: {r['chat_url']}")


def show_summary(conn):
    """Summary of all DM conversations."""
    stats = conn.execute("""
        SELECT
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE status = 'sent') as sent,
            COUNT(*) FILTER (WHERE status = 'skipped') as skipped,
            COUNT(*) FILTER (WHERE conversation_status = 'needs_reply') as needs_reply,
            COUNT(*) FILTER (WHERE conversation_status = 'active') as active,
            COUNT(*) FILTER (WHERE conversation_status = 'converted') as converted,
            COUNT(*) FILTER (WHERE conversation_status = 'stale') as stale,
            COUNT(*) FILTER (WHERE tier = 2) as tier2,
            COUNT(*) FILTER (WHERE tier = 3) as tier3,
            COUNT(DISTINCT their_author) as unique_authors
        FROM dms
    """).fetchone()

    msg_stats = conn.execute("""
        SELECT
            COUNT(*) as total_messages,
            COUNT(*) FILTER (WHERE direction = 'outbound') as outbound,
            COUNT(*) FILTER (WHERE direction = 'inbound') as inbound
        FROM dm_messages
    """).fetchone()

    # Conversations with replies
    with_replies = conn.execute("""
        SELECT COUNT(DISTINCT dm_id) FROM dm_messages WHERE direction = 'inbound'
    """).fetchone()

    print("=== DM Pipeline Summary ===")
    print(f"  Conversations: {stats['total']} total ({stats['sent']} sent, {stats['skipped']} skipped)")
    print(f"  Unique authors: {stats['unique_authors']}")
    print(f"  Status: {stats['needs_reply']} needs_reply, {stats['active']} active, {stats['converted']} converted, {stats['stale']} stale")
    print(f"  Tiers: {stats['tier2']} at T2, {stats['tier3']} at T3")
    print(f"  Messages: {msg_stats['total_messages']} total ({msg_stats['outbound']} outbound, {msg_stats['inbound']} inbound)")
    print(f"  Reply rate: {with_replies[0]}/{stats['sent']} conversations have inbound replies")
    print()

    # Top active conversations (most messages)
    top = conn.execute("""
        SELECT d.id, d.their_author, d.platform, d.message_count, d.tier,
               d.conversation_status, d.last_message_at
        FROM dms d
        WHERE d.message_count > 1
        ORDER BY d.message_count DESC, d.last_message_at DESC
        LIMIT 10
    """).fetchall()

    if top:
        print("  Top conversations by message count:")
        for r in top:
            ts = r['last_message_at'].strftime("%m/%d %H:%M") if r['last_message_at'] else "?"
            print(f"    DM #{r['id']} {r['their_author']} [{r['platform']}] - {r['message_count']} msgs, T{r['tier'] or 1}, {r['conversation_status']} (last: {ts})")


def _gmail_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_PATH, GMAIL_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(GMAIL_TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def _scrub_dashes(s):
    """Replace em/en dashes with commas. Em dashes in email subjects cause
    UTF-8 garbling in some clients, and the user has a no-dashes preference."""
    if not s:
        return s
    return s.replace("\u2014", ",").replace("\u2013", ",")


def _send_escalation_email(conn, dm_id, platform, their_author, reason):
    """Send an escalation email with conversation history.

    Sends from i@m13v.com to NOTIFICATION_EMAIL (defaults to i@m13v.com).
    Subject embeds [DM #N] so ingest can match the reply back to this thread.
    """
    to_email = os.environ.get("NOTIFICATION_EMAIL", "i@m13v.com")

    dm = conn.execute("""
        SELECT d.id, d.tier, d.chat_url, d.project_name, d.target_project,
               d.human_reason, d.flagged_at
        FROM dms d WHERE d.id = %s
    """, (dm_id,)).fetchone()

    messages = conn.execute("""
        SELECT direction, author, content, message_at
        FROM dm_messages WHERE dm_id = %s ORDER BY message_at ASC
    """, (dm_id,)).fetchall()

    history_lines = []
    for msg in messages:
        arrow = ">>" if msg["direction"] == "outbound" else "<<"
        ts = msg["message_at"].strftime("%Y-%m-%d %H:%M") if msg["message_at"] else "?"
        history_lines.append(f"  {arrow} [{ts}] {msg['author']}: {msg['content']}")
    history_text = "\n".join(history_lines) if history_lines else "(no messages logged)"

    project = (dm.get("target_project") or dm.get("project_name") or "unset") if dm else "unset"
    tier = (dm.get("tier") if dm else None) or 1
    chat_url_line = f"Chat URL: {dm['chat_url']}\n" if (dm and dm.get("chat_url")) else ""

    body = (
        f"DM #{dm_id} [{platform}] with {their_author} needs your attention.\n\n"
        f"Reason: {reason}\n"
        f"Tier: {tier}   Project: {project}\n"
        f"{chat_url_line}\n"
        f"=== Conversation History ===\n{history_text}\n\n"
        f"---\n"
        f"Reply to this email to respond. Your reply will be sent as a DM on {platform}.\n"
        f"Keep the [DM #{dm_id}] token in the subject line so the pipeline can route it.\n"
    )

    subject = _scrub_dashes(f"[DM #{dm_id}] {their_author} [{platform}]: {(reason or '')[:120]}")
    body = _scrub_dashes(body)

    msg = MIMEText(body, "plain", "utf-8")
    msg["to"] = to_email
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    try:
        service = _gmail_service()
        result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        gmail_id = result.get("id", "")
        print(f"  Escalation email sent for DM #{dm_id} to {to_email} (gmail id: {gmail_id})")
        return gmail_id
    except Exception as e:
        print(f"  WARNING: Failed to send escalation email for DM #{dm_id}: {e}")
        return None


def flag_human(conn, dm_id, reason):
    """Flag a conversation as needing human attention and send escalation email."""
    row = conn.execute("SELECT platform, their_author, conversation_status FROM dms WHERE id = %s", (dm_id,)).fetchone()
    if not row:
        print(f"ERROR: DM #{dm_id} not found")
        return False

    conn.execute("""
        UPDATE dms SET conversation_status = 'needs_human', human_reason = %s, flagged_at = NOW()
        WHERE id = %s
    """, (reason, dm_id))
    conn.commit()
    print(f"  FLAGGED DM #{dm_id} ({row['their_author']} [{row['platform']}]) for human attention: {reason}")

    _send_escalation_email(conn, dm_id, row['platform'], row['their_author'], reason)
    return True


def show_flagged(conn):
    """Show all conversations flagged for human attention."""
    rows = conn.execute("""
        SELECT d.id, d.platform, d.their_author, d.tier, d.human_reason, d.flagged_at,
               d.chat_url, d.message_count,
               (SELECT content FROM dm_messages WHERE dm_id = d.id ORDER BY message_at DESC LIMIT 1) as last_msg,
               (SELECT direction FROM dm_messages WHERE dm_id = d.id ORDER BY message_at DESC LIMIT 1) as last_dir
        FROM dms d
        WHERE d.conversation_status = 'needs_human'
        ORDER BY d.flagged_at DESC
    """).fetchall()

    if not rows:
        print("No conversations flagged for human attention.")
        return

    print(f"=== {len(rows)} conversations need HUMAN attention ===\n")
    for r in rows:
        ts = r['flagged_at'].strftime("%m/%d %H:%M") if r['flagged_at'] else "?"
        last = (r['last_msg'] or "")[:150]
        print(f"  DM #{r['id']} [{r['platform']}] {r['their_author']} (T{r['tier'] or 1}, {r['message_count']} msgs)")
        print(f"    REASON: {r['human_reason']}")
        print(f"    Flagged: {ts}")
        print(f"    Last msg ({r['last_dir']}): {last}")
        if r['chat_url']:
            print(f"    URL: {r['chat_url']}")
        print()


def set_chat_url(conn, dm_id, url):
    row = conn.execute("SELECT platform FROM dms WHERE id = %s", (dm_id,)).fetchone()
    if not row:
        print(f"  ERROR: DM #{dm_id} not found")
        return
    clean = _valid_chat_url(row["platform"], url)
    if url and not clean:
        print(f"  ERROR: '{url[:120]}' is not a valid {row['platform']} DM-thread URL; refusing to save.")
        print(f"         Expected shapes: reddit=/chat/room/!..., x=/i/chat/..., linkedin=/messaging/thread/...")
        sys.exit(2)
    conn.execute("UPDATE dms SET chat_url = %s WHERE id = %s", (clean, dm_id))
    conn.commit()
    print(f"  Set chat_url for DM #{dm_id}")


def backfill_urls(conn, platform, records):
    """Stamp chat_url onto existing orphan dms rows from a bulk scan.

    records: iterable of dicts with at least {"author": str, "chat_url"|"thread_url": str}.
    Only rows where chat_url is currently NULL are updated. Invalid URLs
    (post permalinks, profile URLs) are skipped by the validator.
    """
    stats = {"updated": 0, "skipped_invalid": 0, "skipped_already_set": 0, "no_match": 0, "ambiguous": 0}
    for rec in records:
        author = (rec.get("author") or rec.get("handle") or "").strip()
        raw = rec.get("chat_url") or rec.get("thread_url") or ""
        if not author:
            continue
        clean = _valid_chat_url(platform, raw)
        if not clean:
            stats["skipped_invalid"] += 1
            continue
        matches = conn.execute(
            "SELECT id, chat_url FROM dms WHERE platform = %s AND LOWER(their_author) = LOWER(%s)",
            (platform, author),
        ).fetchall()
        if not matches:
            stats["no_match"] += 1
            continue
        target = None
        for m in matches:
            if not m["chat_url"]:
                if target is not None:
                    target = "ambiguous"
                    break
                target = m["id"]
        if target == "ambiguous":
            stats["ambiguous"] += 1
            continue
        if target is None:
            stats["skipped_already_set"] += 1
            continue
        conn.execute("UPDATE dms SET chat_url = %s WHERE id = %s AND chat_url IS NULL",
                     (clean, target))
        conn.commit()
        stats["updated"] += 1
    print(f"  backfill-urls [{platform}]: updated={stats['updated']} "
          f"already_set={stats['skipped_already_set']} no_match={stats['no_match']} "
          f"invalid={stats['skipped_invalid']} ambiguous={stats['ambiguous']}")


def set_tier(conn, dm_id, tier):
    conn.execute(
        """
        UPDATE dms
        SET tier = %s,
            first_product_mention_at = CASE
                WHEN %s >= 2 AND first_product_mention_at IS NULL THEN NOW()
                ELSE first_product_mention_at
            END
        WHERE id = %s
        """,
        (tier, tier, dm_id),
    )
    conn.commit()
    print(f"  Set tier={tier} for DM #{dm_id}")


def set_status(conn, dm_id, status):
    conn.execute("UPDATE dms SET conversation_status = %s WHERE id = %s", (status, dm_id))
    conn.commit()
    print(f"  Set conversation_status={status} for DM #{dm_id}")


def set_interest(conn, dm_id, interest):
    conn.execute("UPDATE dms SET interest_level = %s WHERE id = %s", (interest, dm_id))
    conn.commit()
    print(f"  Set interest_level={interest} for DM #{dm_id}")


def set_project(conn, dm_id, project):
    conn.execute("UPDATE dms SET project_name = %s WHERE id = %s", (project, dm_id))
    conn.commit()
    print(f"  Set project_name={project} for DM #{dm_id}")


def set_target_project(conn, dm_id, project):
    conn.execute("UPDATE dms SET target_project = %s WHERE id = %s", (project, dm_id))
    conn.commit()
    print(f"  Set target_project={project} for DM #{dm_id}")


def set_qualification(conn, dm_id, status, notes=None):
    if notes is not None:
        conn.execute(
            "UPDATE dms SET qualification_status = %s, qualification_notes = %s WHERE id = %s",
            (status, notes, dm_id),
        )
    else:
        conn.execute(
            "UPDATE dms SET qualification_status = %s WHERE id = %s",
            (status, dm_id),
        )
    conn.commit()
    suffix = f" (notes: {notes[:60]}...)" if notes else ""
    print(f"  Set qualification_status={status} for DM #{dm_id}{suffix}")


def mark_booking_sent(conn, dm_id):
    conn.execute("UPDATE dms SET booking_link_sent_at = NOW() WHERE id = %s", (dm_id,))
    conn.commit()
    print(f"  Set booking_link_sent_at=NOW() for DM #{dm_id}")


def set_icp_precheck(conn, dm_id, label, project, notes=None):
    """Upsert a per-project ICP verdict into dms.icp_matches (JSONB array).
    Does NOT gate sending. Also syncs icp_precheck TEXT with the entry matching
    the row's current target_project so legacy readers still see a label."""
    import json as _json

    if not project:
        raise ValueError("set_icp_precheck requires project (name from config.json)")

    entry = {
        "project": project,
        "label":   label,
        "notes":   notes,
        "at":      datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    conn.execute(
        """
        UPDATE dms
        SET icp_matches = COALESCE(
            (SELECT jsonb_agg(e) FROM jsonb_array_elements(icp_matches) e
             WHERE e->>'project' IS DISTINCT FROM %s),
            '[]'::jsonb
        ) || %s::jsonb,
            icp_precheck = CASE
                WHEN target_project = %s THEN %s
                ELSE icp_precheck
            END
        WHERE id = %s
        """,
        (project, _json.dumps(entry), project, label, dm_id),
    )

    if notes is not None:
        prefix = f"icp:{project}:{label} - {notes}"
        conn.execute(
            """
            UPDATE dms
            SET qualification_notes = CASE
                WHEN qualification_notes IS NULL OR qualification_notes = ''
                THEN %s
                ELSE qualification_notes || E'\n' || %s
            END
            WHERE id = %s
            """,
            (prefix, prefix, dm_id),
        )
    conn.commit()
    suffix = f" (notes: {notes[:60]}...)" if notes else ""
    print(f"  Upserted icp_matches[{project}]={label} for DM #{dm_id}{suffix}")


def main():
    parser = argparse.ArgumentParser(description="DM conversation tracker")
    sub = parser.add_subparsers(dest="command")

    p_out = sub.add_parser("log-outbound", help="Log outbound message")
    p_out.add_argument("--dm-id", type=int, required=True)
    p_out.add_argument("--content", required=True)
    p_out.add_argument("--author")

    p_ensure = sub.add_parser("ensure-dm",
        help="Return dm_id for (platform, author), creating the row and auto-linking reply_id/post_id from the most recent matching replies row. Prints DM_ID=<n> on stdout.")
    p_ensure.add_argument("--platform", required=True, choices=["reddit", "linkedin", "x", "twitter"])
    p_ensure.add_argument("--author", required=True)
    p_ensure.add_argument("--chat-url", default=None,
        help="Optional chat URL to stamp on the DM row (set only if currently NULL).")
    p_ensure.add_argument("--lookback-hours", type=int, default=720,
        help="How far back to search for a matching replies row when auto-linking (default 720h = 30d).")

    p_in = sub.add_parser("log-inbound", help="Log inbound message")
    p_in.add_argument("--dm-id", type=int, required=True)
    p_in.add_argument("--author", required=True)
    p_in.add_argument("--content", required=True)

    p_hist = sub.add_parser("history", help="Show conversation history")
    p_hist.add_argument("--dm-id", type=int, required=True)

    sub.add_parser("pending", help="Show conversations needing reply")

    p_find = sub.add_parser("find", help="Find DM by author")
    p_find.add_argument("--author", required=True)

    sub.add_parser("summary", help="Pipeline summary")

    p_url = sub.add_parser("set-url", help="Set chat URL")
    p_url.add_argument("--dm-id", type=int, required=True)
    p_url.add_argument("--url", required=True)

    p_backfill = sub.add_parser("backfill-urls",
        help=("Bulk-stamp chat_url onto orphan dms rows from a scanner JSON dump. "
              "Input: a JSON array of {author|handle, chat_url|thread_url} on stdin or --file."))
    p_backfill.add_argument("--platform", required=True, choices=["reddit", "linkedin", "x", "twitter"])
    p_backfill.add_argument("--file", default=None,
        help="Path to JSON file. If omitted, reads from stdin.")

    p_tier = sub.add_parser("set-tier", help="Set conversation tier")
    p_tier.add_argument("--dm-id", type=int, required=True)
    p_tier.add_argument("--tier", type=int, required=True, choices=[1, 2, 3])

    p_status = sub.add_parser("set-status", help="Set conversation status")
    p_status.add_argument("--dm-id", type=int, required=True)
    p_status.add_argument("--status", required=True,
                          choices=["active", "needs_reply", "stale", "converted", "closed", "needs_human"])

    p_interest = sub.add_parser("set-interest", help="Set prospect interest level for product/topic")
    p_interest.add_argument("--dm-id", type=int, required=True)
    p_interest.add_argument("--interest", required=True,
                            choices=["no_response", "general_discussion", "cold", "warm", "hot", "declined", "not_our_prospect"])

    p_flag = sub.add_parser("flag-human", help="Flag conversation for human attention")
    p_flag.add_argument("--dm-id", type=int, required=True)
    p_flag.add_argument("--reason", required=True)

    sub.add_parser("show-flagged", help="Show conversations needing human attention")

    p_resend = sub.add_parser("send-escalation-email",
                              help="Re-send the escalation email for an already-flagged DM (for testing / manual retry)")
    p_resend.add_argument("--dm-id", type=int, required=True)

    p_proj = sub.add_parser("set-project", help="Set project_name (project we recommended)")
    p_proj.add_argument("--dm-id", type=int, required=True)
    p_proj.add_argument("--project", required=True)

    p_tproj = sub.add_parser("set-target-project", help="Set target_project (project we're pursuing)")
    p_tproj.add_argument("--dm-id", type=int, required=True)
    p_tproj.add_argument("--project", required=True)

    p_qual = sub.add_parser("set-qualification", help="Set qualification_status and optional notes")
    p_qual.add_argument("--dm-id", type=int, required=True)
    p_qual.add_argument("--status", required=True,
                         choices=["pending", "asked", "answered", "qualified", "disqualified"])
    p_qual.add_argument("--notes", default=None)

    p_book = sub.add_parser("mark-booking-sent", help="Record that a booking link was shared")
    p_book.add_argument("--dm-id", type=int, required=True)

    p_icp = sub.add_parser("set-icp-precheck", help="Upsert per-project ICP verdict into icp_matches array (no filter)")
    p_icp.add_argument("--dm-id", type=int, required=True)
    p_icp.add_argument("--label", required=True,
                        choices=["icp_match", "icp_miss", "disqualified", "unknown"])
    p_icp.add_argument("--project", required=True,
                       help="Project name from config.json (e.g., 'mk0r', 'Assrt')")
    p_icp.add_argument("--notes", default=None)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    dbmod.load_env()
    conn = dbmod.get_conn()

    if args.command == "log-outbound":
        log_outbound(conn, args.dm_id, args.content, args.author)
    elif args.command == "ensure-dm":
        dm_id, created, linked_reply_id = ensure_dm(
            conn, args.platform, args.author,
            chat_url=args.chat_url, lookback_hours=args.lookback_hours,
        )
        print(f"DM_ID={dm_id}")
        if created:
            if linked_reply_id:
                print(f"  created (linked to replies.id={linked_reply_id})")
            else:
                print("  created (no matching replies row within lookback, reply_id/post_id NULL)")
        else:
            print("  existing")
    elif args.command == "log-inbound":
        log_inbound(conn, args.dm_id, args.author, args.content)
    elif args.command == "history":
        show_history(conn, args.dm_id)
    elif args.command == "pending":
        show_pending(conn)
    elif args.command == "find":
        find_by_author(conn, args.author)
    elif args.command == "summary":
        show_summary(conn)
    elif args.command == "set-url":
        set_chat_url(conn, args.dm_id, args.url)
    elif args.command == "backfill-urls":
        raw = open(args.file).read() if args.file else sys.stdin.read()
        try:
            records = json.loads(raw)
        except Exception as e:
            print(f"ERROR: could not parse JSON input: {e}", file=sys.stderr)
            sys.exit(2)
        if isinstance(records, dict):
            for k in ("conversations", "threads", "dms", "items"):
                if k in records and isinstance(records[k], list):
                    records = records[k]
                    break
        if not isinstance(records, list):
            print("ERROR: expected a JSON array of {author, chat_url} records", file=sys.stderr)
            sys.exit(2)
        backfill_urls(conn, args.platform, records)
    elif args.command == "set-tier":
        set_tier(conn, args.dm_id, args.tier)
    elif args.command == "set-status":
        set_status(conn, args.dm_id, args.status)
    elif args.command == "set-interest":
        set_interest(conn, args.dm_id, args.interest)
    elif args.command == "flag-human":
        flag_human(conn, args.dm_id, args.reason)
    elif args.command == "show-flagged":
        show_flagged(conn)
    elif args.command == "send-escalation-email":
        row = conn.execute("""
            SELECT platform, their_author, human_reason, conversation_status
            FROM dms WHERE id = %s
        """, (args.dm_id,)).fetchone()
        if not row:
            print(f"ERROR: DM #{args.dm_id} not found")
        elif row["conversation_status"] != "needs_human":
            print(f"WARNING: DM #{args.dm_id} is '{row['conversation_status']}', not 'needs_human'. Sending anyway.")
            _send_escalation_email(conn, args.dm_id, row["platform"], row["their_author"],
                                   row["human_reason"] or "(no reason stored)")
        else:
            _send_escalation_email(conn, args.dm_id, row["platform"], row["their_author"],
                                   row["human_reason"] or "(no reason stored)")
    elif args.command == "set-project":
        set_project(conn, args.dm_id, args.project)
    elif args.command == "set-target-project":
        set_target_project(conn, args.dm_id, args.project)
    elif args.command == "set-qualification":
        set_qualification(conn, args.dm_id, args.status, args.notes)
    elif args.command == "mark-booking-sent":
        mark_booking_sent(conn, args.dm_id)
    elif args.command == "set-icp-precheck":
        set_icp_precheck(conn, args.dm_id, args.label, args.project, args.notes)

    conn.close()


if __name__ == "__main__":
    main()
