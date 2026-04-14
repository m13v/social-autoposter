#!/usr/bin/env python3
"""
enrich_twitter_candidates.py

Reads raw tweet JSON from stdin (output of browser scrape),
enriches each tweet with follower count and view count via fxtwitter API,
then outputs enriched JSON to stdout for piping to score_twitter_candidates.py.

Usage:
    cat /tmp/raw_tweets.json | python3 scripts/enrich_twitter_candidates.py | python3 scripts/score_twitter_candidates.py
"""

import json
import re
import sys
import time
import urllib.request


def fetch_fxtwitter(handle, tweet_id):
    """Fetch tweet data from fxtwitter API."""
    url = f"https://api.fxtwitter.com/{handle}/status/{tweet_id}"
    req = urllib.request.Request(url, headers={"User-Agent": "social-autoposter/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  fxtwitter error for {handle}/{tweet_id}: {e}", file=sys.stderr)
        return None


def enrich(tweets):
    enriched = []
    for tweet in tweets:
        url = tweet.get("tweetUrl", tweet.get("tweet_url", ""))
        if not url:
            continue

        # Extract handle and ID from URL
        m = re.search(r"x\.com/([^/]+)/status/(\d+)", url)
        if not m:
            m = re.search(r"twitter\.com/([^/]+)/status/(\d+)", url)
        if not m:
            enriched.append(tweet)
            continue

        handle = m.group(1)
        tweet_id = m.group(2)

        data = fetch_fxtwitter(handle, tweet_id)
        if data and data.get("tweet"):
            t = data["tweet"]
            author = t.get("author", {})
            tweet["author_followers"] = author.get("followers", 0)
            tweet["views"] = t.get("views", 0)
            tweet["likes"] = t.get("likes", tweet.get("likes", 0))
            tweet["retweets"] = t.get("retweets", tweet.get("retweets", 0))
            tweet["replies"] = t.get("replies", tweet.get("replies", 0))
            tweet["bookmarks"] = t.get("bookmarks", tweet.get("bookmarks", 0))
            tweet["handle"] = author.get("screen_name", handle)

        # Normalize field names
        tweet["tweet_url"] = url
        tweet.setdefault("text", tweet.get("tweetText", tweet.get("tweet_text", "")))
        tweet.setdefault("datetime", tweet.get("tweetPostedAt", ""))
        tweet.setdefault("handle", handle)

        enriched.append(tweet)
        time.sleep(0.5)  # rate limit

    return enriched


def main():
    raw = json.load(sys.stdin)
    if not isinstance(raw, list):
        raw = [raw]

    result = enrich(raw)
    json.dump(result, sys.stdout)
    print(f"\nEnriched {len(result)} tweets", file=sys.stderr)


if __name__ == "__main__":
    main()
