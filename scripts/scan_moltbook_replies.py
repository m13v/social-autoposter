#!/usr/bin/env python3
"""Scan Moltbook for new replies to our posts.

Inserts into the `replies` table as 'pending' or 'skipped'. No LLM needed.

Usage:
    python3 scripts/scan_moltbook_replies.py

Requires MOLTBOOK_API_KEY in ~/social-autoposter/.env.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod
from reply_insert import insert_reply as _insert_reply

MIN_WORDS = 5


def word_count(text):
    return len(text.split()) if text else 0


class HttpNotFoundError(Exception):
    pass


def fetch_json(url, headers=None, user_agent="social-autoposter/1.0", retries=3):
    import time
    hdrs = {"User-Agent": user_agent}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"  404 not found: {url}")
                raise HttpNotFoundError(url)
            if e.code == 429 and attempt < retries - 1:
                wait = 30 * (attempt + 1)
                print(f"  429 rate-limited, waiting {wait}s... ({url})")
                time.sleep(wait)
                continue
            print(f"  ERROR fetching {url}: {e}")
            return None
        except HttpNotFoundError:
            raise
        except Exception as e:
            print(f"  ERROR fetching {url}: {e}")
            return None


class MoltbookReplyScanner:
    def __init__(self):
        self.db = dbmod.get_conn()
        self.discovered = 0
        self.skipped = 0
        self.errors = 0

    def insert_reply(self, post_id, comment_id, author, content, comment_url,
                     status="pending", skip_reason=None, moltbook_post_uuid=None):
        result = _insert_reply(
            self.db, post_id, "moltbook", comment_id, author, content, comment_url,
            status=status, skip_reason=skip_reason, moltbook_post_uuid=moltbook_post_uuid,
        )
        if result == "pending":
            self.discovered += 1
        elif result == "skipped":
            self.skipped += 1

    def _extract_post_uuid(self, our_url, thread_url):
        """Extract the Moltbook post UUID from our_url, falling back to thread_url for short IDs."""
        effective_url = our_url
        if not our_url.startswith("http"):
            # Bare fragment (e.g. "#f504d6fb") - reconstruct from thread_url
            if thread_url and thread_url.startswith("http"):
                effective_url = thread_url + our_url
            else:
                return None

        uuid_match = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", effective_url)
        if uuid_match:
            return uuid_match.group()

        # Try thread_url for full UUID (our_url may have short ID)
        if thread_url:
            uuid_match = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", thread_url)
            if uuid_match:
                return uuid_match.group()

        return None

    def _mark_deleted(self, post_id):
        """Mark a Moltbook post as deleted after 2 consecutive 404s."""
        row = self.db.execute(
            "SELECT COALESCE(deletion_detect_count, 0) FROM posts WHERE id=%s", [post_id]
        ).fetchone()
        detect_count = (row[0] if row else 0) + 1
        if detect_count >= 2:
            self.db.execute(
                "UPDATE posts SET status='deleted', deletion_detect_count=%s, status_checked_at=NOW() WHERE id=%s",
                [detect_count, post_id],
            )
            self.db.commit()
            print(f"  DELETED [{post_id}] (Moltbook 404, confirmed after {detect_count} detections)")
        else:
            self.db.execute(
                "UPDATE posts SET deletion_detect_count=%s, status_checked_at=NOW() WHERE id=%s",
                [detect_count, post_id],
            )
            self.db.commit()
            print(f"  DELETION PENDING [{post_id}] (Moltbook 404, detection {detect_count}/2)")

    def scan(self, api_key):
        if not api_key:
            print("MOLTBOOK_API_KEY not set, skipping Moltbook scan")
            return

        print("Scanning Moltbook posts for replies...")
        posts = self.db.execute(
            "SELECT id, our_url, thread_url FROM posts "
            "WHERE platform='moltbook' AND status='active' AND our_url IS NOT NULL",
        ).fetchall()

        skipped_no_uuid = 0
        for post in posts:
            post_id = post["id"]
            post_uuid = self._extract_post_uuid(post["our_url"], post.get("thread_url", ""))
            if not post_uuid:
                skipped_no_uuid += 1
                continue

            try:
                data = fetch_json(
                    f"https://www.moltbook.com/api/v1/posts/{post_uuid}",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            except HttpNotFoundError:
                self._mark_deleted(post_id)
                continue

            if not data or not data.get("success"):
                self.errors += 1
                continue

            # Reset deletion counter on successful fetch
            self.db.execute(
                "UPDATE posts SET deletion_detect_count=0 WHERE id=%s AND COALESCE(deletion_detect_count, 0) > 0",
                [post_id],
            )

            comments = data.get("post", {}).get("comments", [])
            for comment in comments:
                author = comment.get("author", {}).get("name", "")
                content = comment.get("content", "")
                comment_id = comment.get("uuid", comment.get("id", ""))
                comment_url = f"https://www.moltbook.com/post/{post_uuid}#comment-{comment_id}"

                if word_count(content) < MIN_WORDS:
                    self.insert_reply(post_id, comment_id, author, content, comment_url,
                                      status="skipped", skip_reason=f"too_short ({word_count(content)} words)",
                                      moltbook_post_uuid=post_uuid)
                    continue

                self.insert_reply(post_id, comment_id, author, content, comment_url,
                                  moltbook_post_uuid=post_uuid)
                print(f"  NEW: [{post_id}] {author}: {content[:80]}...")

        if skipped_no_uuid:
            print(f"  Skipped {skipped_no_uuid} Moltbook posts (no full UUID available)")

    def finish(self):
        self.db.commit()
        self.db.close()
        print(f"\nMoltbook scan complete: {self.discovered} new pending, {self.skipped} skipped, {self.errors} errors")
        return {"discovered": self.discovered, "skipped": self.skipped, "errors": self.errors}


def main():
    dbmod.load_env()
    api_key = os.environ.get("MOLTBOOK_API_KEY", "")
    scanner = MoltbookReplyScanner()
    scanner.scan(api_key)
    result = scanner.finish()
    # Exit 0 if any new replies found, 1 if none
    sys.exit(0 if result["discovered"] > 0 else 1)


if __name__ == "__main__":
    main()
