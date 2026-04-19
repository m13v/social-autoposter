#!/usr/bin/env python3
"""Harness: verify that InboxScanner._insert promotes a comment to
status='replied' when it's in fetch_own_replies() output.

Uses oga60pv (row 10373 backup) in r/videosurveillance. The row is expected
to have been deleted by the caller; this script re-creates it via the fixed
insert path."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod
from scan_reddit_replies import InboxScanner, fetch_own_replies, load_cookies

OUR = "Deep_Ad1959"
UA = f"social-autoposter/test (u/{OUR})"
POST_ID = 5663  # same post_id the deleted row 10373 used
TARGET_COMMENT_ID = "oga60pv"
TARGET_URL = "https://old.reddit.com/r/videosurveillance/comments/1rrmxkt/cloud_nvr_that_works_w_existing_cams_ai_search/oga60pv/"
TARGET_AUTHOR = "_CapnObvious_"
TARGET_CONTENT = "(fetched separately; not needed for this test)"

dbmod.load_env()
check = dbmod.get_conn().execute(
    "SELECT COUNT(*) FROM replies WHERE their_comment_id=%s", [TARGET_COMMENT_ID]
).fetchone()[0]
if check:
    print(f"ABORT: row with their_comment_id={TARGET_COMMENT_ID} already exists; delete it first.")
    sys.exit(2)

cookie = load_cookies()
if not cookie:
    print("ABORT: no reddit cookies"); sys.exit(2)
m = fetch_own_replies(OUR, cookie, UA)
print(f"own-replies map size: {len(m)}")
print(f"  {TARGET_COMMENT_ID} in map: {TARGET_COMMENT_ID in m}")
if TARGET_COMMENT_ID not in m:
    print("ABORT: target not in map, cannot test the promotion path"); sys.exit(2)

scanner = InboxScanner(OUR, UA, cookie, own_replies_map=m)
# Simulate the path the inbox scan takes when it wants to insert as backfill_old.
scanner._insert(POST_ID, TARGET_COMMENT_ID, TARGET_AUTHOR, TARGET_CONTENT, TARGET_URL,
                status="skipped", skip_reason="backfill_old")
result = scanner.finish()
print(f"scanner counters: already_replied={result.get('already_replied')} "
      f"discovered={result.get('discovered')} backfill_skipped={result.get('backfill_skipped')}")

row = dbmod.get_conn().execute(
    "SELECT id, status, skip_reason, our_reply_id, our_reply_url, replied_at, LEFT(our_reply_content, 80) "
    "FROM replies WHERE their_comment_id=%s", [TARGET_COMMENT_ID],
).fetchone()
print("resulting row:", row)
assert row is not None, "FAIL: no row inserted"
assert row[1] == "replied", f"FAIL: expected status=replied, got {row[1]}"
assert row[3] is not None, "FAIL: our_reply_id not populated"
assert row[4] is not None, "FAIL: our_reply_url not populated"
assert row[5] is not None, "FAIL: replied_at not populated"
print("PASS")
