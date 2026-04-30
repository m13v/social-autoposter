"""Post-generation fact-verification gates.

Three gates that run after the existing structural gates (typecheck, booking
attribution, theme lint) and before commit verification:

  1. verify_dead_urls           - cheap, no LLM. DNS+HEAD every external URL.
  2. verify_time_sensitive_claims - regex scans for "<Vendor> shipped X in
                                    <Month> <Year>" / "<Vendor> raised $X" /
                                    "<Person> named CEO of <Vendor>" patterns,
                                    then a one-shot Claude+WebSearch call to
                                    confirm each match.
  3. extract_and_verify_factual_claims - one Claude+WebSearch call extracts
                                          every numeric/dated/named claim and
                                          flags ones it can't confirm.

Each function returns the same shape as the existing gates:
    {ok: bool, error: str, cleaned: list[str], <gate-specific details>}

On failure, generated files are restored (if tracked) or removed (if
untracked), matching validate_booking_attribution's cleanup path so the
auto-commit cron has nothing to push.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse


URL_RE = re.compile(r'https?://[^\s"\'<>`)\\]+')

PLACEHOLDER_HOST_RE = re.compile(r'<[^>]+>|\{[^}]+\}|\$\{[^}]+\}')

SKIP_HOSTS = {
    "schema.org",
    "example.com",
    "example.org",
    "localhost",
    "127.0.0.1",
}

URL_PROBE_TIMEOUT = 8
URL_PROBE_WORKERS = 8

CLAUDE_VERIFY_TIMEOUT = 240


TIME_SENSITIVE_PATTERNS = [
    re.compile(
        r'\b([A-Z][A-Za-z0-9.&\-]{1,40}(?:\s+[A-Z][A-Za-z0-9.&\-]{1,40}){0,3})\s+'
        r'(?:shipped|launched|released|announced|unveiled|introduced|published|debuted)\s+'
        r'(?:an?\s+|the\s+|its\s+|their\s+)?'
        r'([A-Z][A-Za-z0-9 \-/]{2,80}?)\s+'
        r'in\s+'
        r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+'
        r'(20\d{2})\b'
    ),
    re.compile(
        r'\b([A-Z][A-Za-z0-9.&\-]{1,40}(?:\s+[A-Z][A-Za-z0-9.&\-]{1,40}){0,3})\s+'
        r'raised\s+\$([0-9.]+(?:M|B|million|billion))\b'
    ),
    re.compile(
        r'\b([A-Z][a-z]+\s+[A-Z][a-z]+)\s+(?:named|appointed|promoted to)\s+(?:as\s+)?CEO\b'
    ),
    re.compile(
        r'\b([A-Z][A-Za-z0-9.&\-]{1,40})\s+(?:named|appointed)\s+'
        r'([A-Z][a-z]+\s+[A-Z][a-z]+)\s+(?:as\s+)?CEO\b'
    ),
    re.compile(
        r'\b([A-Z][A-Za-z0-9.&\-]{1,40})\s+acquired\s+([A-Z][A-Za-z0-9.&\-]{1,40})\b'
    ),
]


def _cleanup_files(repo_path: str, file_paths: list[str]) -> list[str]:
    """Restore tracked files / remove untracked ones. Mirrors the pattern in
    validate_booking_attribution so the auto-commit cron has nothing to push.
    """
    root = Path(repo_path)
    cleaned: list[str] = []
    for rel in file_paths:
        abs_path = root / rel
        if not abs_path.exists():
            continue
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel],
            cwd=repo_path, capture_output=True, text=True,
        )
        if tracked.returncode == 0:
            subprocess.run(["git", "restore", "--", rel],
                           cwd=repo_path, capture_output=True, text=True)
            cleaned.append(f"{rel} (restored)")
        else:
            try:
                abs_path.unlink()
                parent = abs_path.parent
                if parent.is_dir() and parent != root and not any(parent.iterdir()):
                    parent.rmdir()
                cleaned.append(f"{rel} (removed)")
            except OSError:
                pass
    return cleaned


def _read_existing(repo_path: str, file_paths: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    root = Path(repo_path)
    for rel in file_paths:
        abs_path = root / rel
        if not abs_path.exists():
            continue
        try:
            out[rel] = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return out


def _is_probable_real_url(url: str) -> bool:
    if PLACEHOLDER_HOST_RE.search(url):
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if not host or host in SKIP_HOSTS:
        return False
    if host.endswith(".local") or host.endswith(".test") or host.endswith(".invalid"):
        return False
    return True


def _probe_url(url: str) -> tuple[str, str | None]:
    """Returns (url, error_or_None). HEAD with GET fallback."""
    try:
        host = urlparse(url).hostname
        if host:
            socket.gethostbyname(host)
    except (socket.gaierror, UnicodeError) as e:
        return url, f"dns: {e}"

    # Statuses that mean "the host answered, the URL exists" even if the
    # response body is gated. Redirects count as alive (the URL resolves to
    # something). 401/403/429 are anti-bot/auth gates, not 'URL is broken'.
    ALIVE_STATUSES = {200, 201, 202, 203, 204, 205, 206,
                      301, 302, 303, 307, 308,
                      401, 403, 429}
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(
                url, method=method,
                headers={"User-Agent": "Mozilla/5.0 (verify_facts/1.0)"},
            )
            with urllib.request.urlopen(req, timeout=URL_PROBE_TIMEOUT) as resp:
                if resp.status in ALIVE_STATUSES or 200 <= resp.status < 400:
                    return url, None
                if method == "GET":
                    return url, f"http {resp.status}"
        except urllib.error.HTTPError as e:
            if e.code in ALIVE_STATUSES:
                return url, None
            if e.code == 405 and method == "HEAD":
                continue
            if method == "GET":
                return url, f"http {e.code}"
        except (urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError) as e:
            if method == "GET":
                return url, f"net: {e}"
        except Exception as e:
            if method == "GET":
                return url, f"err: {type(e).__name__}: {e}"
    return url, None


def verify_dead_urls(repo_path: str, file_paths: list[str],
                     cleanup: bool = True) -> dict:
    """Gate #2: every external URL emitted in the page must resolve and respond.

    Catches the failure mode where the model invented a working-looking
    endpoint (`https://mcp.10xats.com/v1`) and rendered it as a live URL.

    ``cleanup=False`` skips the file-restore step so the caller can hand the
    dead URL list back to the Claude session for a fix-up attempt before
    reverting. Always pass ``cleanup=True`` (the default) on a final/retry
    call so stale files do not linger for the auto-commit cron.
    """
    files = _read_existing(repo_path, file_paths)
    if not files:
        return {"ok": True, "skipped": "no files on disk"}

    candidates: dict[str, list[str]] = {}
    for rel, text in files.items():
        for m in URL_RE.finditer(text):
            url = m.group(0).rstrip(".,;:)")
            if not _is_probable_real_url(url):
                continue
            candidates.setdefault(url, []).append(rel)

    if not candidates:
        return {"ok": True, "skipped": "no probeable urls"}

    dead: list[dict] = []
    with ThreadPoolExecutor(max_workers=URL_PROBE_WORKERS) as ex:
        futures = {ex.submit(_probe_url, u): u for u in candidates}
        for fut in as_completed(futures):
            url, err = fut.result()
            if err:
                dead.append({"url": url, "error": err,
                             "files": candidates[url]})

    if not dead:
        return {"ok": True, "checked": len(candidates)}

    cleaned: list[str] = []
    if cleanup:
        cleaned = _cleanup_files(repo_path, file_paths)
    sample = "; ".join(
        f"{d['url']} -> {d['error']}" for d in dead[:3]
    )
    return {
        "ok": False,
        "error": f"dead urls in generated page ({len(dead)}/{len(candidates)} fail): {sample}",
        "dead_urls": dead,
        "cleaned": cleaned,
    }


def find_time_sensitive_claims(text: str) -> list[dict]:
    """Surface (vendor, claim, year/date) tuples that need verification."""
    found: list[dict] = []
    seen_spans: set[tuple[int, int]] = set()
    for pat in TIME_SENSITIVE_PATTERNS:
        for m in pat.finditer(text):
            span = (m.start(), m.end())
            if any(abs(s[0] - span[0]) < 5 for s in seen_spans):
                continue
            seen_spans.add(span)
            line_no = text.count("\n", 0, m.start()) + 1
            ctx_start = max(0, m.start() - 80)
            ctx_end = min(len(text), m.end() + 80)
            found.append({
                "match": m.group(0),
                "context": text[ctx_start:ctx_end].replace("\n", " ").strip(),
                "line": line_no,
            })
    return found


def _claude_oneshot(prompt: str, cwd: str,
                    allowed_tools: list[str] | None = None,
                    max_turns: int = 8) -> dict:
    """One-shot Claude call with optional WebSearch. Returns parsed JSON or
    {error: ...}. Used by the time-sensitive and claims-extractor gates.
    """
    cmd = ["claude", "-p", prompt,
           "--output-format", "json",
           "--max-turns", str(max_turns),
           "--dangerously-skip-permissions"]
    if allowed_tools:
        cmd += ["--allowed-tools", ",".join(allowed_tools)]
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            timeout=CLAUDE_VERIFY_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {"error": f"claude verify timeout after {CLAUDE_VERIFY_TIMEOUT}s"}
    except FileNotFoundError:
        return {"error": "claude CLI not on PATH"}
    if proc.returncode != 0:
        return {"error": f"claude exit {proc.returncode}: {proc.stderr[-400:]}"}
    try:
        envelope = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as e:
        return {"error": f"claude json parse: {e}: {proc.stdout[-400:]}"}

    payload_text = envelope.get("result") or envelope.get("response") or ""
    if not isinstance(payload_text, str):
        return {"error": f"unexpected claude envelope: {list(envelope)[:8]}"}

    json_match = re.search(r'\{[\s\S]*\}', payload_text)
    if not json_match:
        return {"error": "no json object in claude reply",
                "raw_tail": payload_text[-400:]}
    try:
        return json.loads(json_match.group(0))
    except json.JSONDecodeError as e:
        return {"error": f"inner json parse: {e}",
                "raw_tail": json_match.group(0)[-400:]}


def verify_time_sensitive_claims(repo_path: str,
                                 file_paths: list[str]) -> dict:
    """Gate #3: time-stamped vendor claims must survive a fresh WebSearch.

    Catches the failure mode where the model wrote 'Ashby shipped X in April
    2026' from fuzzy recall and got the date wrong (real date was September
    2025).
    """
    files = _read_existing(repo_path, file_paths)
    if not files:
        return {"ok": True, "skipped": "no files on disk"}

    all_claims: list[dict] = []
    for rel, text in files.items():
        for c in find_time_sensitive_claims(text):
            c["file"] = rel
            all_claims.append(c)

    if not all_claims:
        return {"ok": True, "skipped": "no time-sensitive claims"}

    claims_for_prompt = [
        {"id": i, "claim": c["match"], "context": c["context"]}
        for i, c in enumerate(all_claims[:15])
    ]
    prompt = (
        "You are a fact-checker. For each claim below, run WebSearch with "
        "the vendor name plus the event keywords (acquisition / launch / "
        "release / appointment / fundraise) and confirm: (a) the event "
        "happened, (b) the date/amount stated is correct.\n\n"
        "Claims:\n"
        + json.dumps(claims_for_prompt, indent=2)
        + "\n\n"
        "Reply with EXACTLY ONE JSON object on a line by itself, schema:\n"
        '{"results": [{"id": <int>, "verdict": "ok"|"wrong_date"|'
        '"wrong_amount"|"event_not_found"|"unverifiable", '
        '"correction": "<one short sentence or null>", '
        '"source_url": "<url or null>"}]}\n'
        "If you cannot reach the web at all, return verdict='unverifiable' "
        "for every row."
    )
    reply = _claude_oneshot(
        prompt, cwd=repo_path,
        allowed_tools=["WebSearch", "WebFetch"], max_turns=10,
    )
    if "error" in reply:
        return {"ok": True, "skipped": f"verifier error: {reply['error']}"}

    results = reply.get("results", [])
    failed = [r for r in results
              if r.get("verdict") in ("wrong_date", "wrong_amount",
                                      "event_not_found")]
    if not failed:
        return {"ok": True, "checked": len(claims_for_prompt)}

    by_id = {c["id"]: c for c in claims_for_prompt}
    findings: list[dict] = []
    for r in failed:
        cid = r.get("id")
        src = by_id.get(cid, {})
        findings.append({
            "claim": src.get("claim"),
            "verdict": r.get("verdict"),
            "correction": r.get("correction"),
            "source_url": r.get("source_url"),
            "context": src.get("context"),
        })

    cleaned = _cleanup_files(repo_path, file_paths)
    sample = "; ".join(
        f"{f['claim']!r} -> {f['verdict']}: {f.get('correction','')}"
        for f in findings[:3]
    )
    return {
        "ok": False,
        "error": f"time-sensitive claim mismatch ({len(failed)}/{len(claims_for_prompt)}): {sample}",
        "findings": findings,
        "cleaned": cleaned,
    }


def extract_and_verify_factual_claims(repo_path: str,
                                      file_paths: list[str]) -> dict:
    """Gate #5: extract every numeric / dated / named-entity claim from the
    page, then have Claude+WebSearch confirm or refute each one.

    Catches the residual hallucinations the cheaper gates miss: mis-named
    products ('Greenhouse Real Talent AI' vs the real 'Greenhouse Real
    Talent'), invented metric precision ('172 verified customers, one third
    Fortune 500'), wrong open-source license attribution ('OpenCATS is
    MIT/AGPL'), etc.
    """
    files = _read_existing(repo_path, file_paths)
    if not files:
        return {"ok": True, "skipped": "no files on disk"}

    snippets = "\n\n".join(
        f"=== {rel} ===\n{text}" for rel, text in files.items()
    )
    if len(snippets) > 60000:
        snippets = snippets[:60000] + "\n\n[truncated]"

    prompt = (
        "You are auditing an SEO page for factual accuracy. The page is "
        "below. Do these steps:\n\n"
        "1. Extract every CHECKABLE factual claim. A checkable claim names "
        "a real-world entity (vendor, product, person, customer logo) and "
        "asserts something concrete about it: a date, a price, a customer "
        "count, an integration, a license, a product feature, a fundraise. "
        "Skip claims about the host product itself (which you cannot verify "
        "from outside) and skip subjective claims ('the best', 'easier').\n"
        "2. For each extracted claim, run WebSearch with the entity name + "
        "the asserted fact, read the top 1-2 results, and decide:\n"
        "   - ok: a credible source confirms the claim as written.\n"
        "   - wrong: a credible source contradicts it; provide the correction.\n"
        "   - unsupported: no credible source found in 1-2 searches; the "
        "claim may still be true but cannot be confirmed.\n"
        "3. Return at most 25 claims; prioritize claims about competitors, "
        "third-party products, dates, dollar amounts, customer logos, and "
        "license names.\n\n"
        "Reply with EXACTLY ONE JSON object on a line by itself:\n"
        '{"claims": [{"text": "<verbatim or near-verbatim quote from the '
        'page>", "verdict": "ok"|"wrong"|"unsupported", '
        '"correction": "<one sentence or null>", '
        '"source_url": "<url or null>"}]}\n\n'
        "Only items with verdict='wrong' will fail the build. "
        "verdict='unsupported' is informational.\n\n"
        "Page:\n" + snippets
    )
    reply = _claude_oneshot(
        prompt, cwd=repo_path,
        allowed_tools=["WebSearch", "WebFetch"], max_turns=20,
    )
    if "error" in reply:
        return {"ok": True, "skipped": f"verifier error: {reply['error']}"}

    claims = reply.get("claims", [])
    wrong = [c for c in claims if c.get("verdict") == "wrong"]
    unsupported = [c for c in claims if c.get("verdict") == "unsupported"]

    if not wrong:
        return {"ok": True, "checked": len(claims),
                "unsupported": unsupported}

    cleaned = _cleanup_files(repo_path, file_paths)
    sample = "; ".join(
        f"{c.get('text','')[:80]!r} -> {c.get('correction','')}"
        for c in wrong[:3]
    )
    return {
        "ok": False,
        "error": f"factual claims contradicted ({len(wrong)}/{len(claims)} wrong): {sample}",
        "wrong": wrong,
        "unsupported": unsupported,
        "cleaned": cleaned,
    }


def verify_keyword_directly_answered(repo_path: str,
                                     file_paths: list[str],
                                     keyword: str) -> dict:
    """Gate #4: the page must literally answer the keyword query, or
    transparently document why no answer exists with an authoritative
    verification trail.

    Catches the failure mode where a page ranks for a lookup-shaped keyword
    (e.g. "anthropic pbc vat number", "github copilot pricing", "openai
    headquarters address", "anthropic founded year") but the page punts on
    the exact datum the user came for ("ask them directly", "may or may
    not", "varies"). A real user commented "gbvat number?" on a fazm page
    that did this. Don't be a dead end.

    Logic:
      1. Classify the keyword shape (lookup vs explanatory) via Claude.
      2. For lookup-shaped keywords, scan the page for the literal datum
         in a prominent position (top ~30% of the page or in a clearly
         labelled "answer" / "TLDR" / "verified" callout).
      3. For explanatory-shaped keywords, scan for a 1-3 sentence direct
         answer near the top.
      4. If the answer is genuinely not publicly available, the page must
         either render a "Direct answer (verified <date>)" callout
         documenting an authoritative source check (HMRC, SEC, Companies
         House, Anthropic support docs, etc.) or include the literal
         non-answer with a verification timestamp.

    Skipped failures (no keyword passed, verifier exception, claude
    unreachable) return ok=True with a 'skipped' field so a transient web
    outage never blocks a page.
    """
    if not keyword or not keyword.strip():
        return {"ok": True, "skipped": "no keyword provided"}

    files = _read_existing(repo_path, file_paths)
    if not files:
        return {"ok": True, "skipped": "no files on disk"}

    snippets = "\n\n".join(
        f"=== {rel} ===\n{text}" for rel, text in files.items()
    )
    if len(snippets) > 60000:
        snippets = snippets[:60000] + "\n\n[truncated]"

    prompt = (
        "You are checking whether an SEO page literally answers the keyword "
        "query a user typed into Google. The page is below.\n\n"
        f"Target keyword: {keyword!r}\n\n"
        "Step 1. Classify the keyword shape:\n"
        "  - 'lookup': the user wants ONE specific datum (a number, ID, "
        "code, exact name, address, price, date, version, count). "
        "Examples: 'anthropic pbc vat number', 'github copilot pricing', "
        "'openai headquarters address', 'tesla cik number', 'docker "
        "version latest'.\n"
        "  - 'explanatory': the user wants a concept explained in prose. "
        "Examples: 'how does docker work', 'what is rag', 'why is claude "
        "slow', 'best ai agent for mac'.\n\n"
        "Step 2. Decide if the page answers it. The rules differ by shape.\n"
        "  - lookup: the literal datum (or a clearly-labelled "
        "'verified-not-available' callout naming the authoritative source "
        "and date checked) MUST appear in roughly the first 30% of the "
        "page or in a section explicitly labelled 'answer', 'tldr', "
        "'direct answer', or 'verified <date>'. Burying it on row 18 of a "
        "table at the bottom does NOT count.\n"
        "  - explanatory: a 1-3 sentence direct answer MUST appear in "
        "roughly the first 30% of the page (hero, TLDR, lede, or first "
        "section).\n\n"
        "Step 3. If the literal answer does not exist publicly (e.g. "
        "Anthropic does not publish a UK VAT number), the page is still "
        "OK iff it contains a transparent 'verified <date>' callout that "
        "names the authoritative source it checked (HMRC, SEC EDGAR, "
        "Companies House, GitHub, vendor pricing page, etc.) and the "
        "result. A page that just says 'ask them directly' or 'this may "
        "vary' or 'depends' is a dead end and FAILS.\n\n"
        "Reply with EXACTLY ONE JSON object on a line by itself:\n"
        '{"shape": "lookup"|"explanatory", '
        '"answered": true|false, '
        '"answer_excerpt": "<verbatim 1-3 sentence quote from the page '
        'that delivers the answer, or null if not answered>", '
        '"position": "top"|"middle"|"bottom"|"missing", '
        '"verdict": "answered"|"dead_end"|"buried", '
        '"recommendation": "<one short sentence on what to add or '
        'change>"}\n\n'
        "Only verdict='dead_end' fails the build. 'buried' is a warning "
        "(the answer exists but is not prominent). 'answered' is a pass.\n\n"
        "Page:\n" + snippets
    )
    reply = _claude_oneshot(
        prompt, cwd=repo_path,
        allowed_tools=["WebSearch", "WebFetch"], max_turns=8,
    )
    if "error" in reply:
        return {"ok": True, "skipped": f"verifier error: {reply['error']}"}

    verdict = reply.get("verdict")
    if verdict in ("answered", "buried"):
        return {"ok": True,
                "verdict": verdict,
                "shape": reply.get("shape"),
                "position": reply.get("position"),
                "answer_excerpt": reply.get("answer_excerpt"),
                "recommendation": reply.get("recommendation")}

    if verdict != "dead_end":
        return {"ok": True,
                "skipped": f"unrecognized verdict: {verdict!r}",
                "raw": reply}

    cleaned = _cleanup_files(repo_path, file_paths)
    return {
        "ok": False,
        "error": (f"keyword dead-end: page does not answer {keyword!r}. "
                  f"Recommendation: "
                  f"{(reply.get('recommendation') or '')[:300]}"),
        "shape": reply.get("shape"),
        "verdict": verdict,
        "recommendation": reply.get("recommendation"),
        "cleaned": cleaned,
    }


def main() -> int:
    """CLI for ad-hoc use:
        python3 verify_facts.py <repo_path> <relative_file> [...]
            [--gate=urls|time|claims|answer|all]
            [--keyword=<keyword>] [--no-cleanup]

    Gates default to ALL four. Pass --no-cleanup to keep the file on disk
    on failure (useful for ad-hoc audits; the in-pipeline gates always
    cleanup so the auto-commit cron has nothing to push).
    The 'answer' gate requires --keyword; without it the gate is skipped.
    """
    args = sys.argv[1:]
    gate = "all"
    no_cleanup = False
    keyword = ""
    files: list[str] = []
    repo_path = ""
    for a in args:
        if a.startswith("--gate="):
            gate = a.split("=", 1)[1]
        elif a.startswith("--keyword="):
            keyword = a.split("=", 1)[1]
        elif a == "--no-cleanup":
            no_cleanup = True
        elif not repo_path:
            repo_path = a
        else:
            files.append(a)
    if not repo_path or not files:
        print("usage: verify_facts.py <repo_path> <rel_file> [...] "
              "[--gate=urls|time|claims|answer|all] [--keyword=<kw>] "
              "[--no-cleanup]",
              file=sys.stderr)
        return 2

    if no_cleanup:
        global _cleanup_files
        _cleanup_files = lambda repo, paths: []  # noqa: E731

    results: dict[str, dict] = {}
    if gate in ("urls", "all"):
        results["urls"] = verify_dead_urls(repo_path, files)
    if gate in ("time", "all"):
        results["time_sensitive"] = verify_time_sensitive_claims(repo_path, files)
    if gate in ("claims", "all"):
        results["claims"] = extract_and_verify_factual_claims(repo_path, files)
    if gate in ("answer", "all"):
        results["keyword_answer"] = verify_keyword_directly_answered(
            repo_path, files, keyword)

    print(json.dumps(results, indent=2, default=str))
    return 0 if all(r.get("ok") for r in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
