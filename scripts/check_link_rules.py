#!/usr/bin/env python3
"""Fetch subreddit rules and flag link-ban language. Writes JSON to /tmp/link_rules.json."""
import json, re, sys, time, urllib.request, urllib.error

UA = "social-autoposter-audit/1.0 (by /u/Deep_Ad1959)"

LINK_BAN_PATTERNS = [
    (r"no (photos?|links?|videos?|urls?|images?)", "hard_no_links"),
    (r"(links?|urls?) (are )?not allowed", "hard_no_links"),
    (r"no self[- ]?promotion", "no_self_promo"),
    (r"no (advertising|ads|promo)", "no_self_promo"),
    (r"no (referral|affiliate)", "no_referral"),
    (r"no (blog|youtube|substack|medium) (link|post)", "no_blog_links"),
    (r"links.*require.*approval", "link_requires_approval"),
    (r"link posts? (are )?(banned|disallowed|not permitted)", "hard_no_links"),
    (r"submissions? must be text", "text_only"),
    (r"self[- ]?posts? only", "text_only"),
    (r"(\d+):1 (self[- ]?promotion|promo) (rule|ratio)", "ratio_rule"),
    (r"9:1", "ratio_rule"),
]

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}"}
    except Exception as e:
        return {"_error": str(e)[:80]}

def classify(text):
    if not text:
        return []
    text_l = text.lower()
    hits = []
    for pat, tag in LINK_BAN_PATTERNS:
        if re.search(pat, text_l):
            hits.append(tag)
    return sorted(set(hits))

def check_sub(sub):
    rules = fetch(f"https://www.reddit.com/r/{sub}/about/rules.json")
    about = fetch(f"https://www.reddit.com/r/{sub}/about.json")
    time.sleep(1.5)

    result = {"sub": sub, "rules_text": [], "description": "", "tags": set(), "error": None}

    if rules.get("_error"):
        result["error"] = f"rules: {rules['_error']}"
    else:
        for rule in rules.get("rules", []):
            short = rule.get("short_name", "") or ""
            desc = rule.get("description", "") or ""
            combined = f"{short}. {desc}".strip()
            if combined:
                result["rules_text"].append(combined)
                for tag in classify(combined):
                    result["tags"].add(tag)

    if about.get("_error"):
        if result["error"]:
            result["error"] += f"; about: {about['_error']}"
        else:
            result["error"] = f"about: {about['_error']}"
    else:
        data = about.get("data", {})
        desc = (data.get("public_description", "") or "") + "\n" + (data.get("description", "") or "")
        result["description"] = desc[:2000]
        for tag in classify(desc):
            result["tags"].add(tag)
        result["subreddit_type"] = data.get("subreddit_type", "")
        result["over18"] = data.get("over18", False)

    result["tags"] = sorted(result["tags"])
    return result

def main():
    subs = []
    with open("/tmp/linked_subs.txt") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            name, count = line.split("|")
            subs.append((name, int(count)))

    out = []
    for i, (sub, count) in enumerate(subs, 1):
        print(f"[{i}/{len(subs)}] {sub} ({count} posts)", file=sys.stderr)
        r = check_sub(sub)
        r["linked_post_count"] = count
        out.append(r)

    with open("/tmp/link_rules.json", "w") as f:
        json.dump(out, f, indent=2)

    print(f"\nWrote /tmp/link_rules.json with {len(out)} subs", file=sys.stderr)

if __name__ == "__main__":
    main()
