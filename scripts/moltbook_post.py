#!/usr/bin/env python3
"""
Moltbook post/comment helper with automatic verification.

Usage:
    python3 scripts/moltbook_post.py post --title "..." --content "..." [--submolt technology]
    python3 scripts/moltbook_post.py comment --post-id UUID --content "..."

Handles the obfuscated lobster math CAPTCHA automatically.
"""
import requests, json, re, sys, os, argparse, time

def get_api_key():
    key = os.environ.get("MOLTBOOK_API_KEY")
    if not key:
        env_file = os.path.expanduser("~/social-autoposter/.env")
        if os.path.exists(env_file):
            with open(env_file) as f:
                for line in f:
                    if line.startswith("MOLTBOOK_API_KEY="):
                        key = line.strip().split("=", 1)[1]
                        break
    if not key:
        print("ERROR: MOLTBOOK_API_KEY not found", file=sys.stderr)
        sys.exit(1)
    return key

BASE = "https://www.moltbook.com/api/v1"

NUMBER_WORDS = {
    'zero':0,'one':1,'two':2,'three':3,'four':4,'five':5,'six':6,'seven':7,
    'eight':8,'nine':9,'ten':10,'eleven':11,'twelve':12,'thirteen':13,
    'fourteen':14,'fifteen':15,'sixteen':16,'seventeen':17,'eighteen':18,
    'nineteen':19,'twenty':20,'thirty':30,'forty':40,'fifty':50,'sixty':60,
    'seventy':70,'eighty':80,'ninety':90
}

def solve_challenge(challenge_text):
    """Solve Moltbook's obfuscated lobster math CAPTCHA.

    Strategy: strip ALL non-alpha chars (handles fragmented words like "tH iR tY"),
    scan for number words using greedy longest-first matching, detect operation,
    try all number pairs with all operations via brute force if needed.
    """
    # Strip non-alpha, join everything
    nospace = re.sub(r'[^a-zA-Z]', '', challenge_text).lower()

    # Build regex patterns that match each number word with optional repeated chars
    # e.g., "three" -> "t+h+r+e+e+" matches "tthhrreeee"
    def make_fuzzy_pattern(word):
        return ''.join(c + '+' for c in word)

    sorted_words = sorted(NUMBER_WORDS.keys(), key=len, reverse=True)
    fuzzy_patterns = [(w, re.compile(make_fuzzy_pattern(w))) for w in sorted_words]

    # Scan for number words using fuzzy matching on the raw stripped text
    nums_raw = []
    remaining = nospace
    while remaining:
        found = False
        for word, pattern in fuzzy_patterns:
            m = pattern.match(remaining)
            if m:
                nums_raw.append(NUMBER_WORDS[word])
                remaining = remaining[m.end():]
                found = True
                break
        if not found:
            remaining = remaining[1:]

    # Combine tens+ones (e.g., twenty + three = 23)
    nums = []
    i = 0
    while i < len(nums_raw):
        val = nums_raw[i]
        if val >= 20 and val < 100 and i+1 < len(nums_raw) and nums_raw[i+1] < 10:
            nums.append(val + nums_raw[i+1])
            i += 2
        else:
            nums.append(val)
            i += 1

    # Filter to reasonable candidates (5-999)
    candidates = [n for n in nums if 5 <= n <= 999]
    if len(candidates) < 2:
        candidates = [n for n in nums if n > 0]

    # Detect primary operation (check raw, stripped, and deduped text)
    lower = challenge_text.lower()
    stripped_lower = nospace  # already lowercase stripped
    deduped_lower = re.sub(r'(.)\1+', r'\1', stripped_lower)
    check_texts = [lower, stripped_lower, deduped_lower]
    if any(any(w in t for t in check_texts) for w in ['multipl', 'product', 'times', 'triple', 'double']) or '*' in challenge_text:
        primary = 'mul'
    elif any(any(w in t for t in check_texts) for w in ['differ', 'subtract', 'less', 'minus', 'remain', 'reduc', 'loses', 'lose', 'lost', 'slow']):
        primary = 'sub'
    else:
        primary = 'add'

    return candidates, primary

def verify_with_brute_force(candidates, primary_op, verification_code, headers):
    """Try all number pair + operation combinations to verify."""
    ops = {
        'add': lambda a, b: a + b,
        'sub': lambda a, b: abs(a - b),
        'mul': lambda a, b: a * b,
    }

    # Try primary op first with last two candidates
    op_order = [primary_op] + [o for o in ['add', 'sub', 'mul'] if o != primary_op]

    for op_name in op_order:
        for i in range(len(candidates)):
            for j in range(len(candidates)):
                if i == j:
                    continue
                a, b = candidates[i], candidates[j]
                answer = f"{ops[op_name](a, b):.2f}"
                try:
                    r = requests.post(
                        f"{BASE}/verify",
                        headers=headers,
                        json={"answer": answer, "verification_code": verification_code},
                        timeout=15,
                    )
                    if r.json().get("success"):
                        return True, answer, f"{op_name}({a},{b})"
                except Exception:
                    continue

    return False, None, None

def create_post(title, content, submolt, api_key):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    r = requests.post(
        f"{BASE}/posts",
        headers=headers,
        json={"title": title, "content": content, "type": "text", "submolt_name": submolt},
        timeout=30,
    )

    if r.status_code == 429:
        retry = r.json().get("retry_after_seconds", 160)
        print(f"Rate limited. Retry after {retry}s", file=sys.stderr)
        sys.exit(2)

    d = r.json()
    if not d.get("success"):
        print(f"Create failed: {d.get('message', '')}", file=sys.stderr)
        sys.exit(1)

    post = d["post"]
    post_id = post["id"]
    verification = post.get("verification", {})
    challenge = verification.get("challenge_text", "")
    code = verification.get("verification_code", "")

    print(f"Post created: {post_id}")

    if not challenge or not code:
        print("No verification challenge (unexpected)")
        return post_id, False

    print(f"Challenge: {challenge}")

    candidates, primary_op = solve_challenge(challenge)
    print(f"Numbers found: {candidates}, primary op: {primary_op}")

    if len(candidates) < 2:
        print("ERROR: Could not find enough numbers in challenge", file=sys.stderr)
        return post_id, False

    ok, answer, expr = verify_with_brute_force(candidates, primary_op, code, headers)
    if ok:
        print(f"VERIFIED: {expr} = {answer}")
        return post_id, True
    else:
        print("VERIFICATION FAILED - delete and retry", file=sys.stderr)
        return post_id, False

def create_comment(post_id, content, api_key):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    r = requests.post(
        f"{BASE}/posts/{post_id}/comments",
        headers=headers,
        json={"content": content},
        timeout=30,
    )

    if r.status_code == 429:
        retry = r.json().get("retry_after_seconds", 160)
        print(f"Rate limited. Retry after {retry}s", file=sys.stderr)
        sys.exit(2)

    d = r.json()
    if not d.get("success"):
        msg = d.get("message", "")
        if "suspend" in msg.lower():
            print(f"SUSPENDED: {msg}", file=sys.stderr)
            sys.exit(3)
        print(f"Comment failed: {msg}", file=sys.stderr)
        sys.exit(1)

    comment = d.get("comment", d)
    comment_id = comment.get("id", "?")
    print(f"Comment created: {comment_id}")

    # Comments require verification
    verification = comment.get("verification", d.get("verification", {}))
    if verification.get("challenge_text") and verification.get("verification_code"):
        challenge_text = verification["challenge_text"]
        ver_code = verification["verification_code"]
        print(f"Challenge: {challenge_text}")
        candidates, primary_op = solve_challenge(challenge_text)
        print(f"Numbers found: {candidates}, primary op: {primary_op}")
        if len(candidates) < 2:
            print("ERROR: Not enough numbers found", file=sys.stderr)
            return comment_id, False
        ok, answer, expr = verify_with_brute_force(
            candidates, primary_op, ver_code, headers
        )
        if ok:
            print(f"VERIFIED: {expr} = {answer}")
        else:
            print("VERIFICATION FAILED", file=sys.stderr)
            return comment_id, False

    return comment_id, True

def self_upvote(item_type, item_id, api_key):
    """Self-upvote a post or comment after verification."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if item_type == "post":
        url = f"{BASE}/posts/{item_id}/upvote"
    else:
        url = f"{BASE}/comments/{item_id}/upvote"
    try:
        r = requests.post(url, headers=headers, timeout=15)
        d = r.json()
        if d.get("success") or d.get("upvoted"):
            print(f"Self-upvoted {item_type} {item_id[:12]}")
            return True
        retry = d.get("retry_after_seconds")
        if retry:
            time.sleep(retry + 1)
            r = requests.post(url, headers=headers, timeout=15)
            if r.json().get("success") or r.json().get("upvoted"):
                print(f"Self-upvoted {item_type} {item_id[:12]} (retry)")
                return True
    except Exception as e:
        print(f"Upvote failed: {e}", file=sys.stderr)
    return False


def main():
    parser = argparse.ArgumentParser(description="Moltbook post/comment with auto-verification")
    sub = parser.add_subparsers(dest="action")

    post_p = sub.add_parser("post")
    post_p.add_argument("--title", required=True)
    post_p.add_argument("--content", required=True)
    post_p.add_argument("--submolt", default="general")
    post_p.add_argument("--no-upvote", action="store_true", help="Skip self-upvote")

    comment_p = sub.add_parser("comment")
    comment_p.add_argument("--post-id", required=True)
    comment_p.add_argument("--content", required=True)
    comment_p.add_argument("--no-upvote", action="store_true", help="Skip self-upvote")

    args = parser.parse_args()
    api_key = get_api_key()

    if args.action == "post":
        post_id, verified = create_post(args.title, args.content, args.submolt, api_key)
        if not verified:
            print(f"Deleting unverified post {post_id}...")
            requests.delete(
                f"{BASE}/posts/{post_id}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            sys.exit(1)
        if not args.no_upvote:
            self_upvote("post", post_id, api_key)
        url = f"https://www.moltbook.com/post/{post_id}"
        print(json.dumps({"post_id": post_id, "verified": True, "url": url}))
    elif args.action == "comment":
        comment_id, ok = create_comment(args.post_id, args.content, api_key)
        if ok and not args.no_upvote:
            self_upvote("comment", str(comment_id), api_key)
        url = f"https://www.moltbook.com/post/{args.post_id}#{comment_id}"
        print(json.dumps({"comment_id": str(comment_id), "verified": ok, "url": url}))
    else:
        parser.print_help()
        sys.exit(1)

if __name__ == "__main__":
    main()
