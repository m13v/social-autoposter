#!/usr/bin/env python3
"""Scan Reddit + Moltbook for new replies to our posts/comments.

Inserts into the `replies` table as 'pending' or 'skipped'.
No LLM needed — pure API calls.

Usage:
    python3 scripts/scan_replies.py [--db PATH] [--reddit-account NAME]

Reads config.json for defaults if flags are omitted.
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

STALENESS_DAYS = 30
MIN_WORDS = 5
DEFAULT_DB = os.path.expanduser("~/social-autoposter/social_posts.db")
CONFIG_PATH = os.path.expanduser("~/social-autoposter/config.json")


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def word_count(text):
    return len(text.split()) if text else 0


def is_too_old(created_utc):
    if not created_utc:
        return False
    try:
        comment_time = datetime.fromtimestamp(float(created_utc), tz=timezone.utc)
        return (datetime.now(timezone.utc) - comment_time) > timedelta(days=STALENESS_DAYS)
    except (ValueError, TypeError):
        return False


def fetch_json(url, headers=None, user_agent="social-autoposter/1.0", retries=5):
    hdrs = {"User-Agent": user_agent}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                wait = 30 * (attempt + 1)
                print(f"  429 rate-limited, waiting {wait}s... ({url})")
                time.sleep(wait)
                continue
            print(f"  ERROR fetching {url}: {e}")
            return None
        except Exception as e:
            print(f"  ERROR fetching {url}: {e}")
            return None


class ReplyScanner:
    def __init__(self, db_path, reddit_account, user_agent="social-autoposter/1.0"):
        self.db = sqlite3.connect(db_path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.reddit_account = reddit_account
        self.user_agent = user_agent
        self.skip_authors = {"AutoModerator", "[deleted]", reddit_account}
        self.discovered = 0
        self.skipped = 0
        self.errors = 0

        # Ensure replies table exists
        self.db.execute("""CREATE TABLE IF NOT EXISTS replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER REFERENCES posts(id),
            platform TEXT NOT NULL,
            their_comment_id TEXT NOT NULL,
            their_author TEXT,
            their_content TEXT,
            their_comment_url TEXT,
            our_reply_id TEXT,
            our_reply_content TEXT,
            our_reply_url TEXT,
            parent_reply_id INTEGER REFERENCES replies(id),
            moltbook_post_uuid TEXT,
            moltbook_parent_comment_uuid TEXT,
            depth INTEGER DEFAULT 1,
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending','replied','skipped','error')),
            skip_reason TEXT,
            discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            replied_at TIMESTAMP
        )""")

    def already_tracked(self, platform, comment_id):
        row = self.db.execute(
            "SELECT COUNT(*) FROM replies WHERE platform=? AND their_comment_id=?",
            (platform, str(comment_id)),
        ).fetchone()
        return row[0] > 0

    def insert_reply(self, post_id, platform, comment_id, author, content, comment_url,
                     parent_reply_id=None, depth=1, status="pending", skip_reason=None,
                     moltbook_post_uuid=None, moltbook_parent_comment_uuid=None):
        comment_id = str(comment_id)
        if self.already_tracked(platform, comment_id):
            return

        self.db.execute(
            """INSERT INTO replies
            (post_id, platform, their_comment_id, their_author, their_content, their_comment_url,
             parent_reply_id, depth, status, skip_reason, moltbook_post_uuid, moltbook_parent_comment_uuid)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (post_id, platform, comment_id, author, content, comment_url,
             parent_reply_id, depth, status, skip_reason, moltbook_post_uuid, moltbook_parent_comment_uuid),
        )

        if status == "pending":
            self.discovered += 1
        else:
            self.skipped += 1

        # Commit after each insert to avoid losing data on rate-limit crashes
        self.db.commit()

    def is_our_post(self, post):
        """Detect if this DB row is an original post we authored (not a comment on someone else's thread)."""
        thread_url = post["thread_url"] or ""
        our_url = post["our_url"] or ""
        try:
            thread_author = post["thread_author"] or ""
        except (IndexError, KeyError):
            thread_author = ""
        # Original post: thread_url == our_url, or thread_author is our account
        if thread_url and our_url and thread_url.rstrip("/") == our_url.rstrip("/"):
            return True
        if thread_author and thread_author.lower() in (self.reddit_account.lower(), f"u/{self.reddit_account}".lower()):
            return True
        return False

    def process_reddit_comment(self, cdata, post_id, parent_reply_id=None, depth=1):
        """Process a single Reddit comment and return whether it was added as pending."""
        author = cdata.get("author", "")
        body = cdata.get("body", "")
        comment_id = cdata.get("id", "")
        created = cdata.get("created_utc")
        permalink = cdata.get("permalink", "")
        comment_url = f"https://old.reddit.com{permalink}" if permalink else ""

        if author in self.skip_authors:
            self.insert_reply(post_id, "reddit", comment_id, author, body, comment_url,
                              parent_reply_id=parent_reply_id, depth=depth,
                              status="skipped", skip_reason="filtered_author")
            return False
        if body in ("[deleted]", "[removed]"):
            self.insert_reply(post_id, "reddit", comment_id, author, body, comment_url,
                              parent_reply_id=parent_reply_id, depth=depth,
                              status="skipped", skip_reason="deleted")
            return False
        if word_count(body) < MIN_WORDS:
            self.insert_reply(post_id, "reddit", comment_id, author, body, comment_url,
                              parent_reply_id=parent_reply_id, depth=depth,
                              status="skipped", skip_reason=f"too_short ({word_count(body)} words)")
            return False
        if is_too_old(created):
            self.insert_reply(post_id, "reddit", comment_id, author, body, comment_url,
                              parent_reply_id=parent_reply_id, depth=depth,
                              status="skipped", skip_reason="too_old")
            return False

        self.insert_reply(post_id, "reddit", comment_id, author, body, comment_url,
                          parent_reply_id=parent_reply_id, depth=depth)
        print(f"  NEW (depth {depth}): [{post_id}] u/{author}: {body[:80]}...")
        return True

    def walk_comment_tree(self, children, post_id, parent_reply_id=None, depth=1, max_depth=5):
        """Recursively walk a Reddit comment tree, processing all non-our comments."""
        for child in children:
            if child.get("kind") != "t1":
                continue
            cdata = child.get("data", {})
            author = cdata.get("author", "")

            # For original posts: our own comments in the tree are not replies to track,
            # but we still want to recurse into their children (replies to our replies)
            is_ours = author.lower() == self.reddit_account.lower()

            if not is_ours:
                self.process_reddit_comment(cdata, post_id, parent_reply_id=parent_reply_id, depth=depth)

            # Recurse into nested replies
            if depth < max_depth:
                replies_obj = cdata.get("replies")
                if replies_obj and isinstance(replies_obj, dict):
                    nested = replies_obj.get("data", {}).get("children", [])
                    if nested:
                        self.walk_comment_tree(nested, post_id, parent_reply_id=parent_reply_id, depth=depth + 1, max_depth=max_depth)

    def process_reddit_replies(self, children, post_id, parent_reply_id=None, depth=1):
        """Process a flat list of Reddit comments (non-recursive, used for comment-post scanning)."""
        for child in children:
            if child.get("kind") != "t1":
                continue
            cdata = child.get("data", {})
            self.process_reddit_comment(cdata, post_id, parent_reply_id=parent_reply_id, depth=depth)

    def scan_reddit(self):
        print("Scanning Reddit posts for replies...")
        posts = self.db.execute(
            "SELECT id, our_url, thread_url, thread_title, thread_author FROM posts "
            "WHERE platform='reddit' AND status='active' AND our_url IS NOT NULL"
        ).fetchall()

        for post in posts:
            post_id = post["id"]
            our_url = post["our_url"]
            is_original = self.is_our_post(post)

            if is_original:
                # Original post: fetch the post URL and scan ALL top-level comments + their trees
                json_url = re.sub(r"www\.reddit\.com", "old.reddit.com", our_url).rstrip("/") + ".json"
                data = fetch_json(json_url, user_agent=self.user_agent)
                if not data or not isinstance(data, list) or len(data) < 2:
                    self.errors += 1
                    continue

                children = data[1].get("data", {}).get("children", [])
                if children:
                    print(f"  Scanning original post [{post_id}]: {post['thread_title'][:60]}... ({len(children)} top-level comments)")
                    self.walk_comment_tree(children, post_id, depth=1)
            else:
                # Comment on someone else's thread: fetch our comment URL and scan replies to it
                json_url = re.sub(r"www\.reddit\.com", "old.reddit.com", our_url).rstrip("/") + ".json"
                data = fetch_json(json_url, user_agent=self.user_agent)
                if not data or not isinstance(data, list) or len(data) < 2:
                    self.errors += 1
                    continue

                children = data[1].get("data", {}).get("children", [])
                if not children:
                    continue

                our_comment = children[0].get("data", {})
                replies_obj = our_comment.get("replies")
                if not replies_obj or not isinstance(replies_obj, dict):
                    continue

                reply_children = replies_obj.get("data", {}).get("children", [])
                self.process_reddit_replies(reply_children, post_id)

            time.sleep(8)

        # Scan replies to our previous replies (infinite depth BFS)
        print("\nScanning replies to our previous replies...")
        replied_rows = self.db.execute(
            "SELECT id, platform, our_reply_url, post_id, depth "
            "FROM replies WHERE status='replied' AND our_reply_url IS NOT NULL"
        ).fetchall()

        for row in replied_rows:
            if row["platform"] != "reddit":
                continue
            json_url = re.sub(r"www\.reddit\.com", "old.reddit.com", row["our_reply_url"]).rstrip("/") + ".json"
            data = fetch_json(json_url, user_agent=self.user_agent)
            if not data or not isinstance(data, list) or len(data) < 2:
                continue

            children = data[1].get("data", {}).get("children", [])
            if not children:
                continue

            our_reply_data = children[0].get("data", {})
            replies_obj = our_reply_data.get("replies")
            if not replies_obj or not isinstance(replies_obj, dict):
                continue

            reply_children = replies_obj.get("data", {}).get("children", [])
            self.process_reddit_replies(
                reply_children, row["post_id"],
                parent_reply_id=row["id"], depth=row["depth"] + 1,
            )
            time.sleep(8)

    def scan_moltbook(self, api_key):
        if not api_key:
            print("MOLTBOOK_API_KEY not set, skipping Moltbook scan")
            return

        print("\nScanning Moltbook posts for replies...")
        posts = self.db.execute(
            "SELECT id, our_url FROM posts "
            "WHERE platform='moltbook' AND status='active' AND our_url IS NOT NULL"
        ).fetchall()

        for post in posts:
            post_id = post["id"]
            uuid_match = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", post["our_url"])
            if not uuid_match:
                continue

            data = fetch_json(
                f"https://www.moltbook.com/api/v1/posts/{uuid_match.group()}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if not data or not data.get("success"):
                self.errors += 1
                continue

            comments = data.get("post", {}).get("comments", [])
            for comment in comments:
                author = comment.get("author", {}).get("name", "")
                content = comment.get("content", "")
                comment_id = comment.get("uuid", comment.get("id", ""))
                comment_url = f"https://www.moltbook.com/post/{uuid_match.group()}#comment-{comment_id}"

                if word_count(content) < MIN_WORDS:
                    self.insert_reply(post_id, "moltbook", comment_id, author, content, comment_url,
                                      status="skipped", skip_reason=f"too_short ({word_count(content)} words)",
                                      moltbook_post_uuid=uuid_match.group())
                    continue

                self.insert_reply(post_id, "moltbook", comment_id, author, content, comment_url,
                                  moltbook_post_uuid=uuid_match.group())
                print(f"  NEW: [{post_id}] {author}: {content[:80]}...")

    def finish(self):
        self.db.commit()
        self.db.close()
        print(f"\nScan complete: {self.discovered} new pending, {self.skipped} skipped, {self.errors} errors")
        return {"discovered": self.discovered, "skipped": self.skipped, "errors": self.errors}


def main():
    parser = argparse.ArgumentParser(description="Scan for replies to our social posts")
    parser.add_argument("--db", default=None, help="Path to SQLite database")
    parser.add_argument("--reddit-account", default=None, help="Reddit username")
    args = parser.parse_args()

    config = load_config()
    db_path = args.db or os.path.expanduser(config.get("database", DEFAULT_DB))
    reddit_account = args.reddit_account or config.get("accounts", {}).get("reddit", {}).get("username", "")

    if not reddit_account:
        print("ERROR: Reddit account not configured. Set it in config.json or pass --reddit-account")
        sys.exit(1)

    user_agent = f"social-autoposter/1.0 (u/{reddit_account})"
    scanner = ReplyScanner(db_path, reddit_account, user_agent)
    scanner.scan_reddit()

    moltbook_key = os.environ.get("MOLTBOOK_API_KEY", "")
    scanner.scan_moltbook(moltbook_key)

    result = scanner.finish()
    # Exit with code 0 if any new replies found, 1 if none
    sys.exit(0 if result["discovered"] > 0 else 1)


if __name__ == "__main__":
    main()
