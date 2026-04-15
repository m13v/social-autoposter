#!/usr/bin/env python3
"""
score_twitter_candidates.py

Reads raw tweet data (JSON from stdin or file), calculates virality scores,
and upserts into the twitter_candidates table.

Also expires old candidates (>12h) and prunes posted/expired rows older than 7 days.

Can be called standalone or piped from the scanner:
    echo '[{...}]' | python3 scripts/score_twitter_candidates.py
    python3 scripts/score_twitter_candidates.py --file /tmp/tweets.json
    python3 scripts/score_twitter_candidates.py --expire-only
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod


def calculate_virality_score(tweet):
    """
    Score a tweet's viral potential. Higher = better candidate to reply to.

    Signals (from research + production tuning):
    1. Engagement velocity (eng/hour) - strongest predictor
    2. Retweet ratio > 0.3 = strong viral signal
    3. Reply count is weighted heavily (discussion = visibility for our reply)
    4. Reply-to-like ratio (discussion quality vs one-way broadcast)
    5. Author followers 5K+ sweet spot, big names not penalized
    6. Age penalty: exponential decay with 6h half-life (softer than before)
    """
    likes = tweet.get("likes", 0)
    retweets = tweet.get("retweets", 0)
    replies = tweet.get("replies", 0)
    bookmarks = tweet.get("bookmarks", 0)
    views = tweet.get("views", 0)
    followers = tweet.get("author_followers", 0)

    total_eng = likes + retweets + replies + bookmarks

    # Age in hours
    age_hours = tweet.get("age_hours", 1)
    if age_hours < 0.1:
        age_hours = 0.1

    # 1. Engagement velocity (most important)
    velocity = total_eng / age_hours

    # 2. Retweet ratio (reshare intent)
    rt_ratio = retweets / total_eng if total_eng > 0 else 0

    # 3. Reply activity bonus (active discussion = more visibility for our reply)
    # 15 replies = +1x, 30 = +2x, 60+ = +4x cap
    reply_bonus = min(replies / 15, 4.0)

    # 4. Discussion quality (reply:like ratio). High ratio = real discussion.
    # 0.05 ratio = +0.5x, 0.1+ = +1.0x cap
    discussion_ratio = replies / likes if likes > 0 else 0
    discussion_bonus = min(discussion_ratio * 10, 1.0)

    # 5. Author reach multiplier
    # Sweet spot: 5K+ followers. Big names (KentBeck-class) get full credit,
    # since brand value outweighs the "too competitive" concern.
    if followers < 1000:
        reach_mult = 0.3
    elif followers < 5000:
        reach_mult = 0.6
    elif followers < 50000:
        reach_mult = 1.0
    elif followers < 200000:
        reach_mult = 1.4
    elif followers < 500000:
        reach_mult = 1.3
    else:
        reach_mult = 1.1  # mega accounts still worth it for brand exposure

    # 6. Age decay: half-life of 6 hours (softened from 3h)
    # 3h = 71%, 6h = 50%, 12h = 25%, 18h = 12.5%
    age_decay = math.exp(-0.1155 * age_hours)  # ln(2)/6

    # 7. Retweet ratio bonus
    rt_bonus = 1.0 + min(rt_ratio * 2, 1.0)  # up to 2x for high RT ratio

    # Combine
    score = velocity * reach_mult * age_decay * rt_bonus * (1 + reply_bonus) * (1 + discussion_bonus)

    return round(score, 2), round(velocity, 2), round(rt_ratio, 3)


def match_project(tweet_text, search_topic, config):
    """Match a tweet to the best project based on topic and content."""
    projects = config.get("projects", [])

    # If search_topic maps to a specific project, use that
    topic_lower = (search_topic or "").lower()
    text_lower = (tweet_text or "").lower()

    for proj in projects:
        name = proj.get("name", "")
        topics = [t.lower() for t in proj.get("topics", [])]
        # Direct topic match
        for t in topics:
            if t in topic_lower or t in text_lower:
                return name

    return None


def upsert_candidates(tweets, config):
    """Score and upsert tweet candidates into DB."""
    conn = dbmod.get_conn()

    # Get already-posted thread URLs for dedup
    posted = set()
    rows = conn.execute(
        "SELECT thread_url FROM posts WHERE platform='twitter' AND thread_url IS NOT NULL"
    ).fetchall()
    for row in rows:
        posted.add(row[0])

    inserted = updated = skipped = 0

    for tweet in tweets:
        url = (tweet.get("tweet_url") or tweet.get("tweetUrl") or "").strip()
        if not url:
            continue

        # Skip if we already posted on this thread
        if url in posted:
            skipped += 1
            continue

        # Calculate age
        dt_str = tweet.get("datetime", "")
        if dt_str:
            try:
                posted_at = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                age_hours = (datetime.now(timezone.utc) - posted_at).total_seconds() / 3600
            except ValueError:
                posted_at = None
                age_hours = 24  # unknown age, penalize
        else:
            posted_at = None
            age_hours = 24

        # Skip very old tweets (> 18h). Softened from 12h; we now have a
        # 6h half-life decay, so 18h still scores ~12% — enough that a
        # slow-burn banger can beat a fresh dud.
        if age_hours > 18:
            skipped += 1
            continue

        tweet["age_hours"] = age_hours
        tweet["author_followers"] = tweet.get("author_followers", 0)

        score, velocity, rt_ratio = calculate_virality_score(tweet)

        # Use LLM-assigned project if available, fall back to keyword matching
        project = tweet.get("matched_project") or match_project(
            tweet.get("text", ""),
            tweet.get("search_topic", ""),
            config,
        )

        try:
            conn.execute(
                """
                INSERT INTO twitter_candidates
                    (tweet_url, author_handle, author_followers, tweet_text,
                     tweet_posted_at, likes, retweets, replies, views, bookmarks,
                     engagement_velocity, retweet_ratio, virality_score,
                     search_topic, matched_project, status, discovered_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending', NOW())
                ON CONFLICT (tweet_url) DO UPDATE SET
                    likes = EXCLUDED.likes,
                    retweets = EXCLUDED.retweets,
                    replies = EXCLUDED.replies,
                    views = EXCLUDED.views,
                    bookmarks = EXCLUDED.bookmarks,
                    engagement_velocity = EXCLUDED.engagement_velocity,
                    retweet_ratio = EXCLUDED.retweet_ratio,
                    virality_score = EXCLUDED.virality_score,
                    author_followers = EXCLUDED.author_followers
                """,
                [
                    url,
                    tweet.get("handle", ""),
                    tweet.get("author_followers", 0),
                    (tweet.get("text", "") or "")[:500],
                    posted_at,
                    tweet.get("likes", 0),
                    tweet.get("retweets", 0),
                    tweet.get("replies", 0),
                    tweet.get("views", 0),
                    tweet.get("bookmarks", 0),
                    velocity,
                    rt_ratio,
                    score,
                    tweet.get("search_topic", ""),
                    project,
                ],
            )
            inserted += 1
        except Exception as e:
            print(f"  Error inserting {url}: {e}", file=sys.stderr)
            conn._conn.rollback()
            continue

    conn.commit()

    # Expire old pending candidates (> 18h)
    conn.execute(
        "UPDATE twitter_candidates SET status='expired' "
        "WHERE status='pending' AND discovered_at < NOW() - INTERVAL '18 hours'"
    )
    conn.commit()

    # Prune old rows (> 7 days)
    conn.execute(
        "DELETE FROM twitter_candidates "
        "WHERE status IN ('posted', 'expired', 'skipped') "
        "AND discovered_at < NOW() - INTERVAL '7 days'"
    )
    conn.commit()
    conn.close()

    print(f"Scored: {inserted} upserted, {skipped} skipped (already posted or too old)")
    return inserted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", help="Read tweets from JSON file instead of stdin")
    parser.add_argument("--expire-only", action="store_true", help="Only expire/prune, no scoring")
    args = parser.parse_args()

    config_path = os.path.expanduser("~/social-autoposter/config.json")
    config = {}
    if os.path.exists(config_path):
        with open(config_path) as f:
            config = json.load(f)

    if args.expire_only:
        conn = dbmod.get_conn()
        conn.execute(
            "UPDATE twitter_candidates SET status='expired' "
            "WHERE status='pending' AND discovered_at < NOW() - INTERVAL '18 hours'"
        )
        conn.commit()
        conn.execute(
            "DELETE FROM twitter_candidates "
            "WHERE status IN ('posted', 'expired', 'skipped') "
            "AND discovered_at < NOW() - INTERVAL '7 days'"
        )
        conn.commit()
        conn.close()
        print("Expired/pruned old candidates")
        return

    if args.file:
        with open(args.file) as f:
            tweets = json.load(f)
    else:
        tweets = json.load(sys.stdin)

    if not isinstance(tweets, list):
        tweets = [tweets]

    upsert_candidates(tweets, config)


if __name__ == "__main__":
    main()
