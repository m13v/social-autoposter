#!/usr/bin/env python3
"""Read Reddit Chat state directly from the matrix-js-sdk IndexedDB cache.

Reddit Chat is a Matrix (vanilla v3) client. The entire joined-rooms state,
including per-room unread counts, member displaynames, and recent timeline
events, is persisted client-side in IndexedDB under
`matrix-js-sdk:reddit-chat-sync` -> `sync` store.

Reading that store lets us answer "which rooms have unread messages and what
do they contain" WITHOUT scrolling the virtual sidebar and WITHOUT originating
any API calls. We only read state that the Reddit client itself already
fetched as part of its normal page hydration. This matches the passive CDP
pattern in twitter_browser.py's reply_to_tweet() and stays well inside the
"don't originate calls the human wouldn't" line that took LinkedIn down on
2026-04-17.

Usage:
    python3 reddit_chat_sync.py list-unread             # JSON to stdout
    python3 reddit_chat_sync.py list-unread --pretty    # formatted JSON

The output is an array of records with fields:
    room_id, chat_url, unread_count, room_name, partner_username,
    partner_mxid, last_event_id, last_event_ts, last_event_body,
    last_event_from_us, timeline (array of recent events).

This command is strictly read-only. DB writes come in a later subcommand.

Requires: pip install playwright && playwright install chromium
Shares the reddit-agent Chromium profile + lock used by reddit_browser.py.
"""

import argparse
import atexit
import json
import os
import sys
import time

PROFILE_DIR = os.path.expanduser("~/.claude/browser-profiles/reddit")
LOCK_FILE = os.path.expanduser("~/.claude/reddit-agent-lock.json")
LOCK_EXPIRY = 300
LOCK_WAIT_MAX = 45
LOCK_POLL_INTERVAL = 2
VIEWPORT = {"width": 911, "height": 1016}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

OUR_USERNAME = "Deep_Ad1959"
_config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
if os.path.exists(_config_path):
    try:
        with open(_config_path) as f:
            _cfg = json.load(f)
        OUR_USERNAME = (
            _cfg.get("accounts", {}).get("reddit", {}).get("username", OUR_USERNAME)
        )
    except Exception:
        pass

_LOCK_SESSION_ID = f"python:{os.getpid()}"


def _release_lock():
    try:
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE) as f:
                lock = json.load(f)
            if lock.get("session_id") == _LOCK_SESSION_ID:
                os.remove(LOCK_FILE)
    except (json.JSONDecodeError, OSError):
        pass


atexit.register(_release_lock)


def _acquire_lock():
    deadline = time.time() + LOCK_WAIT_MAX
    while True:
        if os.path.exists(LOCK_FILE):
            try:
                with open(LOCK_FILE) as f:
                    lock = json.load(f)
                age = time.time() - lock.get("timestamp", 0)
                if age >= LOCK_EXPIRY:
                    break
                holder = lock.get("session_id", "unknown")
                if time.time() >= deadline:
                    print(
                        json.dumps(
                            {
                                "success": False,
                                "error": f"Reddit browser locked by session {holder} ({int(age)}s); waited {LOCK_WAIT_MAX}s, giving up.",
                            }
                        )
                    )
                    sys.exit(1)
                time.sleep(LOCK_POLL_INTERVAL)
                continue
            except (json.JSONDecodeError, OSError):
                pass
        break
    with open(LOCK_FILE, "w") as f:
        json.dump(
            {"session_id": _LOCK_SESSION_ID, "timestamp": int(time.time())}, f
        )


# JS we run inside the page. Extracts every joined room that has
# unread_notifications.notification_count > 0, plus enough context to
# reconstruct the conversation.
_EXTRACT_JS = r"""
async () => {
  const REDDIT_SYSTEM_BOT = '@t2_1qwk:reddit.com';

  const openReq = indexedDB.open('matrix-js-sdk:reddit-chat-sync');
  const conn = await new Promise((res, rej) => {
    openReq.onsuccess = () => res(openReq.result);
    openReq.onerror = () => rej(openReq.error);
  });

  const row = await new Promise((res, rej) => {
    const tx = conn.transaction('sync', 'readonly');
    const req = tx.objectStore('sync').getAll();
    req.onsuccess = () => res(req.result[0] || null);
    req.onerror = () => rej(req.error);
  });
  conn.close();

  if (!row || !row.roomsData || !row.roomsData.join) {
    return { ok: false, error: 'no_sync_row', total_joined: 0, unread: [] };
  }

  const join = row.roomsData.join;
  const unread = [];

  for (const [roomId, r] of Object.entries(join)) {
    const nc = (r.unread_notifications && r.unread_notifications.notification_count) || 0;
    const hc = (r.unread_notifications && r.unread_notifications.highlight_count) || 0;
    if (nc === 0 && hc === 0) continue;

    const stateEvents = (r.state && r.state.events) || [];
    const memberEvents = stateEvents.filter(e => e.type === 'm.room.member');
    const nameEv = stateEvents.find(e => e.type === 'm.room.name');
    const roomName = nameEv ? (nameEv.content && nameEv.content.name) || null : null;

    // Identify our mxid and the partner mxid. The Reddit system bot
    // (@t2_1qwk:reddit.com) is a member of every room and must be excluded
    // from partner resolution. Our mxid is identified by displayname match
    // to OUR_USERNAME.
    const ourMxid = memberEvents.find(m =>
      m.content && m.content.displayname === %OUR_USERNAME_LITERAL%
    )?.state_key || null;

    const partnerMember = memberEvents.find(m =>
      m.state_key !== ourMxid &&
      m.state_key !== REDDIT_SYSTEM_BOT &&
      m.content && m.content.displayname
    );

    const timeline = (r.timeline && r.timeline.events) || [];
    // Last human message
    const lastMsg = [...timeline]
      .reverse()
      .find(e => e.type === 'm.room.message');

    // Return the last ~30 timeline events so the caller has enough context
    // to log each new message without re-fetching. We don't return every
    // event to keep the payload reasonable on old rooms.
    const recentTimeline = timeline.slice(-30).map(e => ({
      event_id: e.event_id,
      ts: e.origin_server_ts,
      sender: e.sender,
      type: e.type,
      body: (e.content && e.content.body) || null,
      msgtype: (e.content && e.content.msgtype) || null,
      from_us: e.sender === ourMxid,
    }));

    unread.push({
      room_id: roomId,
      chat_url: 'https://www.reddit.com/chat/room/' + encodeURIComponent(roomId),
      unread_count: nc,
      highlight_count: hc,
      room_name: roomName,
      partner_username: (partnerMember && partnerMember.content && partnerMember.content.displayname) || null,
      partner_mxid: (partnerMember && partnerMember.state_key) || null,
      our_mxid: ourMxid,
      last_event_id: (lastMsg && lastMsg.event_id) || null,
      last_event_ts: (lastMsg && lastMsg.origin_server_ts) || null,
      last_event_body: (lastMsg && lastMsg.content && lastMsg.content.body) || null,
      last_event_from_us: (lastMsg && lastMsg.sender === ourMxid) || false,
      timeline: recentTimeline,
    });
  }

  // Sort by unread_count desc then last_event_ts desc so operators see the
  // loudest threads first.
  unread.sort((a, b) =>
    (b.unread_count - a.unread_count) ||
    ((b.last_event_ts || 0) - (a.last_event_ts || 0))
  );

  return {
    ok: true,
    total_joined: Object.keys(join).length,
    unread_room_count: unread.length,
    total_unread_messages: unread.reduce((s, r) => s + r.unread_count, 0),
    next_batch: row.nextBatch || null,
    unread,
  };
}
""".replace("%OUR_USERNAME_LITERAL%", json.dumps(OUR_USERNAME))


def list_unread(hydration_wait_ms=8000, nav_retries=2):
    """Open /chat in a headless Chromium on the reddit profile, wait for
    matrix-js-sdk to finish its incremental sync, then read IndexedDB.

    Returns the parsed dict from _EXTRACT_JS (or an {ok: false, error} record).
    """
    from playwright.sync_api import sync_playwright

    _acquire_lock()

    try:
        with sync_playwright() as p:
            # Retry on Chromium SingletonLock collisions (MCP holds the OS-level
            # profile lock for its whole lifetime; our JSON lock can expire
            # while the OS lock is still held). Same pattern reddit_browser.py
            # uses.
            deadline = time.time() + LOCK_WAIT_MAX
            last_err = None
            context = None
            while True:
                try:
                    context = p.chromium.launch_persistent_context(
                        PROFILE_DIR,
                        headless=True,
                        args=["--disable-blink-features=AutomationControlled"],
                        viewport=VIEWPORT,
                        user_agent=USER_AGENT,
                    )
                    break
                except Exception as e:
                    last_err = e
                    if time.time() >= deadline:
                        return {
                            "ok": False,
                            "error": f"chromium profile locked by another process; waited {LOCK_WAIT_MAX}s: {e}",
                        }
                    time.sleep(LOCK_POLL_INTERVAL)

            try:
                page = context.new_page()
                # Navigate; occasionally /chat bounces to a room-specific URL
                # which is fine because the IndexedDB state is global across
                # the whole reddit-chat-sync DB, not per-room.
                last_nav_err = None
                for attempt in range(nav_retries + 1):
                    try:
                        page.goto("https://www.reddit.com/chat", wait_until="domcontentloaded", timeout=30000)
                        break
                    except Exception as e:
                        last_nav_err = e
                        if attempt == nav_retries:
                            return {"ok": False, "error": f"navigate_failed: {e}"}
                        time.sleep(2)

                # Let matrix-js-sdk hydrate from its cached state and fire an
                # incremental /_matrix/client/v3/sync?since=<token>. 8s is
                # plenty for the delta-sync case since cached state already
                # covers 737 joined rooms.
                page.wait_for_timeout(hydration_wait_ms)

                result = page.evaluate(_EXTRACT_JS)
                return result
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    finally:
        _release_lock()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command")

    p_list = sub.add_parser(
        "list-unread",
        help="Emit JSON of every Matrix room with unread notifications (read-only).",
    )
    p_list.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON (human-readable). Default is compact.",
    )
    p_list.add_argument(
        "--hydration-ms",
        type=int,
        default=8000,
        help="Milliseconds to wait after /chat navigation for matrix-js-sdk to incremental-sync (default 8000).",
    )

    args = ap.parse_args()

    if args.command == "list-unread":
        result = list_unread(hydration_wait_ms=args.hydration_ms)
        if args.pretty:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(result, ensure_ascii=False))
        sys.exit(0 if result.get("ok") else 1)

    ap.print_help()
    sys.exit(2)


if __name__ == "__main__":
    main()
