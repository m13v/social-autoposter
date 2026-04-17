#!/usr/bin/env python3
"""Scan Reddit for new replies to our posts/comments.

Inserts into the `replies` table as 'pending' or 'skipped'. No LLM needed.

Usage:
    python3 scripts/scan_reddit_replies.py [--reddit-account NAME]

Reads config.json for defaults if flags are omitted.
"""

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod
from reddit_tools import RateLimitedError, _wait_if_needed, _write_ratelimit
from reply_insert import insert_reply as _insert_reply

STALENESS_DAYS = 30
MIN_WORDS = 5
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


class HttpNotFoundError(Exception):
    pass


def fetch_reddit_json(url, user_agent="social-autoposter/1.0"):
    # Shares /tmp/reddit_ratelimit.json with reddit_tools; raises RateLimitedError
    # when reset > 15s so callers can skip instead of hammering.
    _wait_if_needed()
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    delay = 4
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                remaining = float(resp.headers.get("X-Ratelimit-Remaining", 100))
                reset = float(resp.headers.get("X-Ratelimit-Reset", 0))
                _write_ratelimit(remaining, reset)
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"  404 not found: {url}")
                raise HttpNotFoundError(url)
            if e.code == 429:
                reset = float(e.headers.get("X-Ratelimit-Reset", 60))
                _write_ratelimit(0, reset)
                if reset > 15:
                    raise RateLimitedError(reset)
                wait = int(reset) + 2
                print(f"  429 rate-limited, waiting {wait}s... ({url})")
                time.sleep(wait)
                continue
            if attempt < 2:
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            print(f"  ERROR fetching {url}: {e}")
            return None
        except HttpNotFoundError:
            raise
        except Exception as e:
            if attempt < 2:
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            print(f"  ERROR fetching {url}: {e}")
            return None
    return None


class RedditReplyScanner:
    def __init__(self, reddit_account, user_agent="social-autoposter/1.0", excluded_authors=None):
        self.db = dbmod.get_conn()
        self.reddit_account = reddit_account
        self.user_agent = user_agent
        self.skip_authors = {"AutoModerator", "[deleted]", reddit_account}
        if excluded_authors:
            self.skip_authors.update(excluded_authors)
        self.discovered = 0
        self.skipped = 0
        self.errors = 0

    def insert_reply(self, post_id, comment_id, author, content, comment_url,
                     parent_reply_id=None, depth=1, status="pending", skip_reason=None):
        result = _insert_reply(
            self.db, post_id, "reddit", comment_id, author, content, comment_url,
            parent_reply_id=parent_reply_id, depth=depth, status=status, skip_reason=skip_reason,
        )
        if result == "pending":
            self.discovered += 1
        elif result == "skipped":
            self.skipped += 1

    def is_our_post(self, post):
        """Detect if this DB row is an original post we authored (not a comment on someone else's thread)."""
        thread_url = post["thread_url"] or ""
        our_url = post["our_url"] or ""
        try:
            thread_author = post["thread_author"] or ""
        except (IndexError, KeyError):
            thread_author = ""
        if thread_url and our_url and thread_url.rstrip("/") == our_url.rstrip("/"):
            return True
        if thread_author and thread_author.lower() in (self.reddit_account.lower(), f"u/{self.reddit_account}".lower()):
            return True
        return False

    def process_comment(self, cdata, post_id, parent_reply_id=None, depth=1):
        """Process a single Reddit comment and return whether it was added as pending."""
        author = cdata.get("author", "")
        body = cdata.get("body", "")
        comment_id = cdata.get("id", "")
        created = cdata.get("created_utc")
        permalink = cdata.get("permalink", "")
        comment_url = f"https://old.reddit.com{permalink}" if permalink else ""

        if author in self.skip_authors or author.lower() in self.skip_authors:
            self.insert_reply(post_id, comment_id, author, body, comment_url,
                              parent_reply_id=parent_reply_id, depth=depth,
                              status="skipped", skip_reason="filtered_author")
            return False
        if body in ("[deleted]", "[removed]"):
            self.insert_reply(post_id, comment_id, author, body, comment_url,
                              parent_reply_id=parent_reply_id, depth=depth,
                              status="skipped", skip_reason="deleted")
            return False
        if word_count(body) < MIN_WORDS:
            self.insert_reply(post_id, comment_id, author, body, comment_url,
                              parent_reply_id=parent_reply_id, depth=depth,
                              status="skipped", skip_reason=f"too_short ({word_count(body)} words)")
            return False
        if is_too_old(created):
            self.insert_reply(post_id, comment_id, author, body, comment_url,
                              parent_reply_id=parent_reply_id, depth=depth,
                              status="skipped", skip_reason="too_old")
            return False

        self.insert_reply(post_id, comment_id, author, body, comment_url,
                          parent_reply_id=parent_reply_id, depth=depth)
        print(f"  NEW (depth {depth}): [{post_id}] u/{author}: {body[:80]}...")
        return True

    def process_replies(self, children, post_id, parent_reply_id=None, depth=1):
        """Process a flat list of Reddit comments (non-recursive, used for comment-post scanning)."""
        for child in children:
            if child.get("kind") != "t1":
                continue
            cdata = child.get("data", {})
            self.process_comment(cdata, post_id, parent_reply_id=parent_reply_id, depth=depth)

    def scan(self):
        print("Scanning Reddit posts for replies...")
        posts = self.db.execute(
            "SELECT id, our_url, thread_url, thread_title, thread_author, "
            "posted_at, COALESCE(scan_no_change_count, 0) as scan_no_change_count FROM posts "
            "WHERE platform='reddit' AND status='active' AND our_url IS NOT NULL AND our_url != '' AND our_url LIKE 'http%%'"
        ).fetchall()

        # Shuffle so a rate-limit short-circuit mid-run doesn't always starve the
        # same tail of posts; next run's shuffle picks a different starting set.
        posts = list(posts)
        random.shuffle(posts)
        print(f"  Scanning {len(posts)} active reddit posts (shuffled order)")

        skipped = 0
        consecutive_errors = 0
        stopped_early = False
        for post in posts:
            post_id = post["id"]
            our_url = post["our_url"]
            is_original = self.is_our_post(post)

            # Skip posts where the last 2+ scans found no new replies AND post is older than 3 days
            posted_at = post.get("posted_at")
            no_change = post["scan_no_change_count"]
            if no_change >= 2 and posted_at:
                age = datetime.now(timezone.utc) - (posted_at.replace(tzinfo=timezone.utc) if posted_at.tzinfo is None else posted_at)
                if age > timedelta(days=3):
                    skipped += 1
                    continue

            pre_count = self.discovered

            if is_original:
                # Original post: only collect top-level comments (direct replies to our post).
                # Do NOT recurse into reply trees — those are conversations between other users.
                # The BFS scan below handles replies to our own replies at any depth.
                json_url = re.sub(r"www\.reddit\.com", "old.reddit.com", our_url).rstrip("/") + ".json"
                try:
                    data = fetch_reddit_json(json_url, user_agent=self.user_agent)
                except HttpNotFoundError:
                    self.errors += 1
                    consecutive_errors = 0
                    continue
                except RateLimitedError as e:
                    print(f"  Rate limited ({int(e.reset_seconds)}s), stopping reddit scan")
                    stopped_early = True
                    break
                if not data or not isinstance(data, list) or len(data) < 2:
                    self.errors += 1
                    consecutive_errors += 1
                    if consecutive_errors >= 3:
                        print(f"  {consecutive_errors} consecutive fetch failures, stopping reddit scan")
                        stopped_early = True
                        break
                    continue
                consecutive_errors = 0

                children = data[1].get("data", {}).get("children", [])
                if children:
                    print(f"  Scanning original post [{post_id}]: {(post['thread_title'] or '')[:60]}... ({len(children)} top-level comments)")
                    self.process_replies(children, post_id, depth=1)
            else:
                # Comment on someone else's thread: fetch our comment URL and scan replies to it
                json_url = re.sub(r"www\.reddit\.com", "old.reddit.com", our_url).rstrip("/") + ".json"
                try:
                    data = fetch_reddit_json(json_url, user_agent=self.user_agent)
                except HttpNotFoundError:
                    self.errors += 1
                    consecutive_errors = 0
                    continue
                except RateLimitedError as e:
                    print(f"  Rate limited ({int(e.reset_seconds)}s), stopping reddit scan")
                    stopped_early = True
                    break
                if not data or not isinstance(data, list) or len(data) < 2:
                    self.errors += 1
                    consecutive_errors += 1
                    if consecutive_errors >= 3:
                        print(f"  {consecutive_errors} consecutive fetch failures, stopping reddit scan")
                        stopped_early = True
                        break
                    continue
                consecutive_errors = 0

                children = data[1].get("data", {}).get("children", [])
                if not children:
                    continue

                our_comment = children[0].get("data", {})
                replies_obj = our_comment.get("replies")
                if not replies_obj or not isinstance(replies_obj, dict):
                    continue

                reply_children = replies_obj.get("data", {}).get("children", [])
                self.process_replies(reply_children, post_id)

            # Track whether this scan found new replies for this post
            found_new = self.discovered - pre_count
            if found_new > 0:
                self.db.execute("UPDATE posts SET scan_no_change_count = 0 WHERE id = %s", (post_id,))
            else:
                self.db.execute("UPDATE posts SET scan_no_change_count = COALESCE(scan_no_change_count, 0) + 1 WHERE id = %s", (post_id,))
            self.db.commit()

            time.sleep(3)

        if skipped:
            print(f"  Skipped {skipped} stable posts (2+ scans with no new replies, older than 3 days)")

        if stopped_early:
            print("  Skipping BFS scan of replies-to-replies due to rate limit / errors")
            return

        # Scan replies to our previous replies (infinite depth BFS)
        print("\nScanning replies to our previous replies...")
        replied_rows = self.db.execute(
            "SELECT id, platform, our_reply_url, post_id, depth "
            "FROM replies WHERE status='replied' AND our_reply_url IS NOT NULL",
        ).fetchall()

        reddit_rows = [r for r in replied_rows if r["platform"] == "reddit"
                       and (r["our_reply_url"] or "").startswith("http")]
        random.shuffle(reddit_rows)
        print(f"  Scanning {len(reddit_rows)} replied reddit rows (shuffled order)")

        consecutive_errors_bfs = 0
        for row in reddit_rows:
            our_reply_url = row["our_reply_url"]
            json_url = re.sub(r"www\.reddit\.com", "old.reddit.com", our_reply_url).rstrip("/") + ".json"
            try:
                data = fetch_reddit_json(json_url, user_agent=self.user_agent)
            except HttpNotFoundError:
                consecutive_errors_bfs = 0
                continue
            except RateLimitedError as e:
                print(f"  Rate limited ({int(e.reset_seconds)}s), stopping BFS scan")
                break
            if not data or not isinstance(data, list) or len(data) < 2:
                consecutive_errors_bfs += 1
                if consecutive_errors_bfs >= 3:
                    print(f"  {consecutive_errors_bfs} consecutive fetch failures, stopping BFS scan")
                    break
                continue
            consecutive_errors_bfs = 0

            children = data[1].get("data", {}).get("children", [])
            if not children:
                continue

            our_reply_data = children[0].get("data", {})
            replies_obj = our_reply_data.get("replies")
            if not replies_obj or not isinstance(replies_obj, dict):
                continue

            reply_children = replies_obj.get("data", {}).get("children", [])
            self.process_replies(
                reply_children, row["post_id"],
                parent_reply_id=row["id"], depth=row["depth"] + 1,
            )
            time.sleep(3)

    def finish(self):
        self.db.commit()
        self.db.close()
        print(f"\nReddit scan complete: {self.discovered} new pending, {self.skipped} skipped, {self.errors} errors")
        return {"discovered": self.discovered, "skipped": self.skipped, "errors": self.errors}


def main():
    parser = argparse.ArgumentParser(description="Scan Reddit for replies to our posts")
    parser.add_argument("--reddit-account", default=None, help="Reddit username")
    args = parser.parse_args()

    config = load_config()
    reddit_account = args.reddit_account or config.get("accounts", {}).get("reddit", {}).get("username", "")

    if not reddit_account:
        print("ERROR: Reddit account not configured. Set it in config.json or pass --reddit-account")
        sys.exit(1)

    dbmod.load_env()
    user_agent = f"social-autoposter/1.0 (u/{reddit_account})"
    excluded_authors = {a.lower() for a in config.get("exclusions", {}).get("authors", [])}
    scanner = RedditReplyScanner(reddit_account, user_agent, excluded_authors=excluded_authors)
    scanner.scan()
    result = scanner.finish()
    # Exit 0 if any new replies found, 1 if none
    sys.exit(0 if result["discovered"] > 0 else 1)


if __name__ == "__main__":
    main()
