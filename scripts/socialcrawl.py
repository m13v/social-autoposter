#!/usr/bin/env python3
"""SocialCrawl API helpers for Twitter engagement fetching.

API key resolution order:
  1. SOCIALCRAWL_API_KEY env var
  2. macOS keychain: `security find-generic-password -s socialcrawl-api-key -w`
"""

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request


_API_KEY = None


def _get_api_key():
    global _API_KEY
    if _API_KEY:
        return _API_KEY
    key = os.environ.get("SOCIALCRAWL_API_KEY")
    if not key:
        try:
            key = subprocess.check_output(
                ["security", "find-generic-password", "-s", "socialcrawl-api-key", "-w"],
                stderr=subprocess.DEVNULL, timeout=5,
            ).decode().strip()
        except Exception:
            key = None
    _API_KEY = key
    return key


def _get(path, params):
    key = _get_api_key()
    if not key:
        print("  socialcrawl: no API key (env SOCIALCRAWL_API_KEY or keychain socialcrawl-api-key)", file=sys.stderr)
        return None
    qs = urllib.parse.urlencode(params)
    url = f"https://www.socialcrawl.dev{path}?{qs}"
    req = urllib.request.Request(url, headers={"x-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  socialcrawl error {path}: {e}", file=sys.stderr)
        return None


def _safe_int(v):
    if v is None:
        return 0
    try:
        return int(v)
    except (ValueError, TypeError):
        return 0


def fetch_own_tweets_batch(handle):
    """Return {tweet_id: engagement_dict} for the handle's 100 tweets from this endpoint.

    NOT real-time: SocialCrawl's upstream scraper for this endpoint returns data
    that lags by several months (tested 2026-04-18: newest tweets returned were
    from Aug-Nov 2025). The response is also heavily cached server-side. Use
    fetch_tweet_by_url for real-time engagement.

    Cost: 1 credit. Returns {} on failure.
    """
    r = _get("/v1/twitter/user/tweets", {"handle": handle})
    if not r or not r.get("success"):
        return {}
    out = {}
    for item in r.get("data", {}).get("items", []):
        tid = item.get("rest_id")
        if not tid:
            continue
        legacy = item.get("legacy") or {}
        views = _safe_int((item.get("views") or {}).get("count"))
        out[str(tid)] = {
            "views": views,
            "likes": _safe_int(legacy.get("favorite_count")),
            "retweets": _safe_int(legacy.get("retweet_count")),
            "replies": _safe_int(legacy.get("reply_count")),
            "bookmarks": _safe_int(legacy.get("bookmark_count")),
        }
    return out


def fetch_tweet_by_url(url):
    """Return engagement dict for a single tweet, or None if the tweet is gone.

    Cost: 1 credit. Returns None for deleted/suspended/not-found tweets.
    Returns {} (empty-but-not-None) on transient API error so caller can distinguish.
    """
    r = _get("/v1/twitter/tweet", {"url": url})
    if r is None:
        return {}
    if not r.get("success"):
        err_type = (r.get("error") or {}).get("type", "")
        if err_type in ("NOT_FOUND", "TWEET_NOT_FOUND", "RESOURCE_NOT_FOUND"):
            return None
        return {}
    post = (r.get("data") or {}).get("post")
    if not post:
        return None
    eng = post.get("engagement") or {}
    return {
        "views": _safe_int(eng.get("views")),
        "likes": _safe_int(eng.get("likes")),
        "retweets": _safe_int(eng.get("shares")),
        "replies": _safe_int(eng.get("comments")),
        "bookmarks": _safe_int(eng.get("saves")),
    }
