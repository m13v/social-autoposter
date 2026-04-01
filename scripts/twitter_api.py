#!/usr/bin/env python3
"""Twitter/X API wrapper for social-autoposter.

Read-only operations via tweepy: search tweets, fetch mentions, get tweet details.
Writing (posting/replying) is done via browser automation because Twitter API
returns 403 on replies due to "who can reply" permissions.

Requires env vars: TWITTER_BEARER_TOKEN (for all read operations),
TWITTER_API_KEY + TWITTER_API_KEY_SECRET + TWITTER_ACCESS_TOKEN +
TWITTER_ACCESS_TOKEN_SECRET (for get_me only)
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ENV_PATH = os.path.expanduser("~/social-autoposter/.env")


def load_env():
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())


load_env()

import tweepy


def get_read_client():
    """Client for reading (search, get_tweet, get_mentions). Uses bearer token."""
    return tweepy.Client(bearer_token=os.environ["TWITTER_BEARER_TOKEN"])


def get_write_client():
    """Client for writing (create_tweet, delete_tweet). Uses OAuth 1.0a."""
    return tweepy.Client(
        consumer_key=os.environ["TWITTER_API_KEY"],
        consumer_secret=os.environ["TWITTER_API_KEY_SECRET"],
        access_token=os.environ["TWITTER_ACCESS_TOKEN"],
        access_token_secret=os.environ["TWITTER_ACCESS_TOKEN_SECRET"],
    )


def get_me():
    """Get authenticated user info."""
    client = get_write_client()
    resp = client.get_me(user_fields=["id", "username", "name"])
    return resp.data


def search_recent_tweets(query, max_results=25):
    """Search recent tweets. Returns list of tweet dicts."""
    client = get_read_client()
    resp = client.search_recent_tweets(
        query=query,
        max_results=max(10, min(max_results, 100)),
        tweet_fields=["id", "text", "author_id", "created_at", "public_metrics", "conversation_id"],
        expansions=["author_id"],
        user_fields=["username", "name"],
    )
    if not resp.data:
        return []

    # Build author lookup
    users = {}
    if resp.includes and "users" in resp.includes:
        for u in resp.includes["users"]:
            users[u.id] = u.username

    results = []
    for tweet in resp.data:
        results.append({
            "id": str(tweet.id),
            "text": tweet.text,
            "author_id": str(tweet.author_id),
            "author_username": users.get(tweet.author_id, ""),
            "created_at": str(tweet.created_at) if tweet.created_at else "",
            "likes": tweet.public_metrics.get("like_count", 0) if tweet.public_metrics else 0,
            "retweets": tweet.public_metrics.get("retweet_count", 0) if tweet.public_metrics else 0,
            "replies": tweet.public_metrics.get("reply_count", 0) if tweet.public_metrics else 0,
            "conversation_id": str(tweet.conversation_id) if tweet.conversation_id else "",
            "url": f"https://x.com/{users.get(tweet.author_id, 'i')}/status/{tweet.id}",
        })
    return results


def get_mentions(user_id, since_id=None, max_results=100):
    """Get mentions of a user. Returns list of tweet dicts."""
    client = get_read_client()
    kwargs = {
        "id": user_id,
        "max_results": min(max_results, 100),
        "tweet_fields": ["id", "text", "author_id", "created_at", "conversation_id", "in_reply_to_user_id", "referenced_tweets"],
        "expansions": ["author_id", "referenced_tweets.id"],
        "user_fields": ["username", "name"],
    }
    if since_id:
        kwargs["since_id"] = since_id

    resp = client.get_users_mentions(**kwargs)
    if not resp.data:
        return []

    users = {}
    if resp.includes and "users" in resp.includes:
        for u in resp.includes["users"]:
            users[u.id] = u.username

    ref_tweets = {}
    if resp.includes and "tweets" in resp.includes:
        for t in resp.includes["tweets"]:
            ref_tweets[t.id] = t.text

    results = []
    for tweet in resp.data:
        # Determine what tweet this is replying to
        replied_to_id = None
        if tweet.referenced_tweets:
            for ref in tweet.referenced_tweets:
                if ref.type == "replied_to":
                    replied_to_id = str(ref.id)

        results.append({
            "id": str(tweet.id),
            "text": tweet.text,
            "author_id": str(tweet.author_id),
            "author_username": users.get(tweet.author_id, ""),
            "created_at": str(tweet.created_at) if tweet.created_at else "",
            "conversation_id": str(tweet.conversation_id) if tweet.conversation_id else "",
            "replied_to_id": replied_to_id,
            "replied_to_text": ref_tweets.get(int(replied_to_id), "") if replied_to_id else "",
            "url": f"https://x.com/{users.get(tweet.author_id, 'i')}/status/{tweet.id}",
        })
    return results




def get_tweet(tweet_id):
    """Get a single tweet by ID."""
    client = get_read_client()
    resp = client.get_tweet(
        tweet_id,
        tweet_fields=["id", "text", "author_id", "created_at", "public_metrics", "conversation_id"],
        expansions=["author_id"],
        user_fields=["username"],
    )
    if not resp.data:
        return None
    users = {}
    if resp.includes and "users" in resp.includes:
        for u in resp.includes["users"]:
            users[u.id] = u.username
    t = resp.data
    return {
        "id": str(t.id),
        "text": t.text,
        "author_id": str(t.author_id),
        "author_username": users.get(t.author_id, ""),
        "url": f"https://x.com/{users.get(t.author_id, 'i')}/status/{t.id}",
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Twitter API CLI")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("me")

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--max", type=int, default=10)

    s = sub.add_parser("mentions")
    s.add_argument("--since-id", default=None)
    s.add_argument("--max", type=int, default=20)

    s = sub.add_parser("get")
    s.add_argument("tweet_id")

    args = parser.parse_args()

    import json

    if args.cmd == "me":
        me = get_me()
        print(json.dumps({"id": str(me.id), "username": me.username, "name": me.name}, indent=2))

    elif args.cmd == "search":
        results = search_recent_tweets(args.query, max_results=args.max)
        print(json.dumps(results, indent=2))

    elif args.cmd == "mentions":
        me = get_me()
        results = get_mentions(me.id, since_id=args.since_id, max_results=args.max)
        print(json.dumps(results, indent=2))

    elif args.cmd == "get":
        result = get_tweet(args.tweet_id)
        print(json.dumps(result, indent=2))

    else:
        parser.print_help()
