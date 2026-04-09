#!/usr/bin/env python3
"""Find candidate tweets to reply to via Twitter API search.

Replaces browser-based tweet discovery with programmatic API calls.
No LLM, no browser needed — pure Python + tweepy.

Usage:
    python3 scripts/find_tweets.py --project Fazm
    python3 scripts/find_tweets.py --project OMI --max 20
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db as dbmod
import twitter_api

CONFIG_PATH = os.path.expanduser("~/social-autoposter/config.json")


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def get_already_posted():
    """Return set of tweet status IDs we've already posted on."""
    conn = dbmod.get_conn()
    rows = conn.execute(
        "SELECT thread_url FROM posts WHERE platform='twitter' AND status='active'"
    ).fetchall()
    posted = set()
    for row in rows:
        url = row[0] if isinstance(row, (list, tuple)) else row["thread_url"]
        # Extract status ID from URL
        parts = url.rstrip("/").split("/")
        if parts:
            posted.add(parts[-1])
    conn.close()
    return posted


def main():
    parser = argparse.ArgumentParser(description="Find tweets to reply to via API")
    parser.add_argument("--project", default=None, help="Project name to filter topics")
    parser.add_argument("--max", type=int, default=10, help="Max results per topic")
    parser.add_argument("--json-output", action="store_true", help="Output as JSON array")
    args = parser.parse_args()

    config = load_config()
    exclusions = config.get("exclusions", {})
    excluded_accounts = {a.lower() for a in exclusions.get("twitter_accounts", [])}
    excluded_accounts.add("m13v_")  # skip our own tweets

    # Get topics for the project
    if args.project:
        projects = config.get("projects", [])
        project_config = next((p for p in projects if p.get("name") == args.project), None)
        if project_config:
            topics = project_config.get("twitter_topics", config.get("twitter_topics", []))
        else:
            topics = config.get("twitter_topics", [])
    else:
        topics = config.get("twitter_topics", [])

    if not topics:
        print("No twitter_topics found in config.json", file=sys.stderr)
        sys.exit(1)

    already_posted = get_already_posted()
    candidates = []
    seen_ids = set()

    for topic in topics:
        # Build exclusion string
        exclude_str = " ".join(f"-from:{acct}" for acct in excluded_accounts)
        query = f"{topic} {exclude_str} -is:retweet lang:en".strip()

        try:
            tweets = twitter_api.search_recent_tweets(query, max_results=args.max)
        except Exception as e:
            print(f"  ERROR searching '{topic}': {e}", file=sys.stderr)
            continue

        for t in tweets:
            # Skip if already posted or already in candidates
            if t["id"] in already_posted or t["id"] in seen_ids:
                continue
            seen_ids.add(t["id"])
            # Skip excluded authors
            if t["author_username"].lower() in excluded_accounts:
                continue
            # Skip low engagement (minimum 10 likes for meaningful reach)
            if t["likes"] < 10:
                continue

            candidates.append({
                "platform": "twitter",
                "url": t["url"],
                "tweet_id": t["id"],
                "conversation_id": t.get("conversation_id", t["id"]),
                "title": t["text"][:120],
                "full_text": t["text"],
                "author": t["author_username"],
                "author_followers": t.get("author_followers", 0),
                "likes": t["likes"],
                "retweets": t["retweets"],
                "replies": t["replies"],
                "impressions": t.get("impressions", 0),
                "search_topic": topic,
                "discovery_method": "api_search",
            })

    # Sort by reach score: likes + retweets + follower bonus
    # Follower count is log-scaled so a 100K account doesn't completely
    # dominate, but still ranks higher than a 100-follower account.
    import math
    def reach_score(c):
        follower_bonus = math.log10(max(c.get("author_followers", 0), 1))
        return c["likes"] + c["retweets"] + follower_bonus * 5
    candidates.sort(key=reach_score, reverse=True)

    # Enrich top candidates with existing replies so Claude knows what
    # others already said and can add a unique angle instead of repeating.
    enriched = candidates[:args.max]  # only enrich the ones we'll actually use
    for c in enriched:
        conv_id = c.get("conversation_id") or c.get("tweet_id", "")
        if conv_id:
            try:
                top_replies = twitter_api.get_conversation_replies(conv_id, max_results=5)
                if top_replies:
                    c["existing_replies"] = top_replies
            except Exception:
                pass

    if args.json_output:
        print(json.dumps(candidates, indent=2))
    else:
        print(f"\n=== Found {len(candidates)} Twitter candidates ===\n")
        for i, c in enumerate(candidates, 1):
            print(f"{i}. @{c['author']} ({c['likes']}♥ {c['retweets']}🔁)")
            print(f"   {c['title']}")
            print(f"   {c['url']}")
            print(f"   Topic: {c['search_topic']}")
            print()


if __name__ == "__main__":
    main()
