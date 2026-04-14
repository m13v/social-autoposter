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
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod

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
    """Raised when a fetch returns HTTP 404."""
    pass


def fetch_json(url, headers=None, user_agent="social-autoposter/1.0", retries=3):
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


class ReplyScanner:
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

    def already_tracked(self, platform, comment_id):
        row = self.db.execute(
            "SELECT COUNT(*) FROM replies WHERE platform=%s AND their_comment_id=%s",
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
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
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

        if author in self.skip_authors or author.lower() in self.skip_authors:
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
            "SELECT id, our_url, thread_url, thread_title, thread_author, "
            "posted_at, COALESCE(scan_no_change_count, 0) as scan_no_change_count FROM posts "
            "WHERE platform='reddit' AND status='active' AND our_url IS NOT NULL AND our_url != '' AND our_url LIKE 'http%%'"
        ).fetchall()

        skipped = 0
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
                    data = fetch_json(json_url, user_agent=self.user_agent)
                except HttpNotFoundError:
                    self.errors += 1
                    continue
                if not data or not isinstance(data, list) or len(data) < 2:
                    self.errors += 1
                    continue

                children = data[1].get("data", {}).get("children", [])
                if children:
                    print(f"  Scanning original post [{post_id}]: {(post['thread_title'] or '')[:60]}... ({len(children)} top-level comments)")
                    self.process_reddit_replies(children, post_id, depth=1)
            else:
                # Comment on someone else's thread: fetch our comment URL and scan replies to it
                json_url = re.sub(r"www\.reddit\.com", "old.reddit.com", our_url).rstrip("/") + ".json"
                try:
                    data = fetch_json(json_url, user_agent=self.user_agent)
                except HttpNotFoundError:
                    self.errors += 1
                    continue
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

        # Scan replies to our previous replies (infinite depth BFS)
        print("\nScanning replies to our previous replies...")
        replied_rows = self.db.execute(
            "SELECT id, platform, our_reply_url, post_id, depth "
            "FROM replies WHERE status='replied' AND our_reply_url IS NOT NULL",
        ).fetchall()

        for row in replied_rows:
            if row["platform"] != "reddit":
                continue
            # Skip non-URL values like 'posted' that would crash urllib
            our_reply_url = row["our_reply_url"] or ""
            if not our_reply_url.startswith("http"):
                continue
            json_url = re.sub(r"www\.reddit\.com", "old.reddit.com", our_reply_url).rstrip("/") + ".json"
            try:
                data = fetch_json(json_url, user_agent=self.user_agent)
            except HttpNotFoundError:
                continue
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
            time.sleep(3)

    def scan_github_issues(self):
        """Scan GitHub issues for new comments after ours."""
        print("\nScanning GitHub issues for replies...")
        posts = self.db.execute(
            "SELECT id, our_url, thread_url, thread_title FROM posts "
            "WHERE platform='github_issues' AND status='active' AND our_url IS NOT NULL "
            "AND our_url LIKE '%%issuecomment%%'"
        ).fetchall()

        if not posts:
            print("  No GitHub issue comments to scan")
            return

        # Group by issue URL to avoid scanning the same issue multiple times
        issues = {}
        for post in posts:
            # Extract repo and issue number from thread_url
            # e.g. https://github.com/owner/repo/issues/123
            match = re.match(r"https://github\.com/([^/]+/[^/]+)/issues/(\d+)", post["thread_url"])
            if not match:
                continue
            repo = match.group(1)
            issue_num = match.group(2)
            key = f"{repo}/{issue_num}"
            if key not in issues:
                issues[key] = []
            issues[key].append(post)

        github_user = "m13v"
        config = load_config()
        github_user = config.get("accounts", {}).get("github", {}).get("username", github_user)

        for issue_key, issue_posts in issues.items():
            repo, issue_num = issue_key.rsplit("/", 1)

            # Get our highest comment ID for this issue to find newer comments
            our_comment_ids = []
            for p in issue_posts:
                cid_match = re.search(r"issuecomment-(\d+)", p["our_url"])
                if cid_match:
                    our_comment_ids.append(int(cid_match.group(1)))
            if not our_comment_ids:
                continue
            max_our_id = max(our_comment_ids)

            # Use the first post's ID for linking replies
            post_id = issue_posts[0]["id"]
            title = issue_posts[0]["thread_title"] or ""

            # Fetch all comments on the issue via gh CLI
            import subprocess
            try:
                result = subprocess.run(
                    ["gh", "api", f"repos/{repo}/issues/{issue_num}/comments",
                     "--jq", f'[.[] | select(.id > {max_our_id}) | {{id: .id, user: .user.login, body: .body, url: .html_url, created: .created_at}}]'],
                    capture_output=True, text=True, timeout=15
                )
                if result.returncode != 0:
                    self.errors += 1
                    continue
                comments = json.loads(result.stdout) if result.stdout.strip() else []
            except Exception as e:
                print(f"  ERROR scanning {issue_key}: {e}")
                self.errors += 1
                continue

            for comment in comments:
                author = comment.get("user", "")
                if author == github_user:
                    continue  # Skip our own comments

                body = comment.get("body", "")
                comment_id = str(comment.get("id", ""))
                comment_url = comment.get("url", "")

                if word_count(body) < MIN_WORDS:
                    self.insert_reply(post_id, "github_issues", comment_id, author, body, comment_url,
                                      status="skipped", skip_reason=f"too_short ({word_count(body)} words)")
                    continue

                self.insert_reply(post_id, "github_issues", comment_id, author, body, comment_url)
                print(f"  NEW: [{post_id}] @{author} on {issue_key}: {body[:80]}...")

            time.sleep(1)  # Light rate limiting for gh CLI

    def _extract_moltbook_post_uuid(self, our_url, thread_url):
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

    def _mark_moltbook_deleted(self, post_id):
        """Use detection counter to mark a Moltbook post as deleted after 2 consecutive 404s."""
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

    def scan_moltbook(self, api_key):
        if not api_key:
            print("MOLTBOOK_API_KEY not set, skipping Moltbook scan")
            return

        print("\nScanning Moltbook posts for replies...")
        posts = self.db.execute(
            "SELECT id, our_url, thread_url FROM posts "
            "WHERE platform='moltbook' AND status='active' AND our_url IS NOT NULL",
        ).fetchall()

        skipped_no_uuid = 0
        for post in posts:
            post_id = post["id"]
            post_uuid = self._extract_moltbook_post_uuid(post["our_url"], post.get("thread_url", ""))
            if not post_uuid:
                skipped_no_uuid += 1
                continue

            try:
                data = fetch_json(
                    f"https://www.moltbook.com/api/v1/posts/{post_uuid}",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            except HttpNotFoundError:
                self._mark_moltbook_deleted(post_id)
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
                    self.insert_reply(post_id, "moltbook", comment_id, author, content, comment_url,
                                      status="skipped", skip_reason=f"too_short ({word_count(content)} words)",
                                      moltbook_post_uuid=post_uuid)
                    continue

                self.insert_reply(post_id, "moltbook", comment_id, author, content, comment_url,
                                  moltbook_post_uuid=post_uuid)
                print(f"  NEW: [{post_id}] {author}: {content[:80]}...")

        if skipped_no_uuid:
            print(f"  Skipped {skipped_no_uuid} Moltbook posts (no full UUID available)")

    def finish(self):
        self.db.commit()
        self.db.close()
        print(f"\nScan complete: {self.discovered} new pending, {self.skipped} skipped, {self.errors} errors")
        return {"discovered": self.discovered, "skipped": self.skipped, "errors": self.errors}


def main():
    parser = argparse.ArgumentParser(description="Scan for replies to our social posts")
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
    scanner = ReplyScanner(reddit_account, user_agent, excluded_authors=excluded_authors)
    scanner.scan_reddit()
    # GitHub issues scanning moved to scripts/scan_github_replies.py (separate pipeline)

    moltbook_key = os.environ.get("MOLTBOOK_API_KEY", "")
    scanner.scan_moltbook(moltbook_key)

    result = scanner.finish()
    # Exit with code 0 if any new replies found, 1 if none
    sys.exit(0 if result["discovered"] > 0 else 1)


if __name__ == "__main__":
    main()
