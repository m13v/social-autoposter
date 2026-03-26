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
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

CONFIG_PATH = os.path.expanduser("~/social-autoposter/config.json")


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

    conn.execute("""
        INSERT INTO dm_messages (dm_id, direction, author, content, message_at, logged_at)
        VALUES (%s, 'outbound', %s, %s, NOW(), NOW())
    """, (dm_id, author, content))

    conn.execute("""
        UPDATE dms SET last_message_at = NOW(), message_count = message_count + 1,
                       conversation_status = 'active'
        WHERE id = %s
    """, (dm_id,))
    conn.commit()
    print(f"  Logged outbound to {row['their_author']} (DM #{dm_id})")
    return True


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


def flag_human(conn, dm_id, reason):
    """Flag a conversation as needing human attention."""
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
    conn.execute("UPDATE dms SET chat_url = %s WHERE id = %s", (url, dm_id))
    conn.commit()
    print(f"  Set chat_url for DM #{dm_id}")


def set_tier(conn, dm_id, tier):
    conn.execute("UPDATE dms SET tier = %s WHERE id = %s", (tier, dm_id))
    conn.commit()
    print(f"  Set tier={tier} for DM #{dm_id}")


def set_status(conn, dm_id, status):
    conn.execute("UPDATE dms SET conversation_status = %s WHERE id = %s", (status, dm_id))
    conn.commit()
    print(f"  Set conversation_status={status} for DM #{dm_id}")


def main():
    parser = argparse.ArgumentParser(description="DM conversation tracker")
    sub = parser.add_subparsers(dest="command")

    p_out = sub.add_parser("log-outbound", help="Log outbound message")
    p_out.add_argument("--dm-id", type=int, required=True)
    p_out.add_argument("--content", required=True)
    p_out.add_argument("--author")

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

    p_tier = sub.add_parser("set-tier", help="Set conversation tier")
    p_tier.add_argument("--dm-id", type=int, required=True)
    p_tier.add_argument("--tier", type=int, required=True, choices=[1, 2, 3])

    p_status = sub.add_parser("set-status", help="Set conversation status")
    p_status.add_argument("--dm-id", type=int, required=True)
    p_status.add_argument("--status", required=True,
                          choices=["active", "needs_reply", "stale", "converted", "closed", "needs_human"])

    p_flag = sub.add_parser("flag-human", help="Flag conversation for human attention")
    p_flag.add_argument("--dm-id", type=int, required=True)
    p_flag.add_argument("--reason", required=True)

    sub.add_parser("show-flagged", help="Show conversations needing human attention")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    dbmod.load_env()
    conn = dbmod.get_conn()

    if args.command == "log-outbound":
        log_outbound(conn, args.dm_id, args.content, args.author)
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
    elif args.command == "set-tier":
        set_tier(conn, args.dm_id, args.tier)
    elif args.command == "set-status":
        set_status(conn, args.dm_id, args.status)
    elif args.command == "flag-human":
        flag_human(conn, args.dm_id, args.reason)
    elif args.command == "show-flagged":
        show_flagged(conn)

    conn.close()


if __name__ == "__main__":
    main()
