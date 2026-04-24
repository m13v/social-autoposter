#!/usr/bin/env python3
"""Shared engagement style definitions for all platforms.

Centralizes style taxonomy, platform-specific guidance, content rules,
and prompt generation so every pipeline (post_reddit, engage_reddit,
run-twitter-cycle, run-linkedin, engage-twitter, engage-linkedin) references
a single source of truth.

Usage:
    from engagement_styles import VALID_STYLES, REPLY_STYLES, get_styles_prompt, get_content_rules, get_anti_patterns
"""

# ── Style taxonomy ──────────────────────────────────────────────────

STYLES = {
    "critic": {
        "description": "Point out what's missing, flawed, or naive. Reframe the problem.",
        "example": "The part that breaks down is...",
        "best_in": {
            "reddit": ["r/Entrepreneur", "r/smallbusiness", "r/startups"],
            "twitter": ["tech", "startup", "business"],
            "linkedin": ["strategy", "leadership", "operations"],
        },
        "note": "NEVER just nitpick; offer a non-obvious insight.",
    },
    "storyteller": {
        "description": "Pure first-person narrative with specific details (numbers, dates, names). Lead with failure/surprise, not success.",
        "example": "we tracked this for six months and found... / i made this exact mistake when...",
        "best_in": {
            "reddit": ["r/startups", "r/Meditation", "r/vipassana"],
            "twitter": ["personal growth", "founder stories"],
            "linkedin": ["career", "leadership", "lessons learned"],
        },
        "note": "NEVER pivot to a product pitch.",
    },
    "pattern_recognizer": {
        "description": "Name the pattern or phenomenon. Authority through pattern recognition, not credentials.",
        "example": "This is called X / I've seen this play out dozens of times across Y.",
        "best_in": {
            "reddit": ["r/ExperiencedDevs", "r/programming", "r/webdev"],
            "twitter": ["dev", "engineering", "tech trends"],
            "linkedin": ["industry analysis", "tech leadership"],
        },
        "note": "Authority through pattern recognition, not credentials.",
    },
    "curious_probe": {
        "description": "One specific follow-up question about the most interesting detail. Include 'curious because...' context.",
        "example": "curious because we ran into something similar...",
        "best_in": {
            "reddit": ["r/startups", "r/SaaS", "niche subs"],
            "twitter": ["niche topics", "founder discussions"],
            "linkedin": ["thought leadership", "niche B2B"],
        },
        "note": "ONE question only. Never multiple.",
    },
    "contrarian": {
        "description": "Take a clear opposing position backed by experience.",
        "example": "Everyone recommends X. I've done X for Y years and it's wrong.",
        "best_in": {
            "reddit": ["r/Entrepreneur", "r/ExperiencedDevs"],
            "twitter": ["hot takes", "industry debates"],
            "linkedin": ["industry debates", "contrarian leadership"],
        },
        "note": "Must have credible evidence. Empty hot takes get destroyed.",
    },
    "data_point_drop": {
        "description": "Share one specific, believable metric. Let the number do the talking.",
        "example": "$12k in a month (not 'a lot of money')",
        "best_in": {
            "reddit": ["r/Entrepreneur", "r/startups", "r/SaaS"],
            "twitter": ["growth", "revenue", "metrics"],
            "linkedin": ["results", "case studies"],
        },
        "note": "No links. Numbers must be believable, not impressive.",
    },
    "snarky_oneliner": {
        "description": "Short, sharp, emotionally resonant observation (1 sentence max). Validates a shared frustration.",
        "example": "(witty one-liner that nails the shared pain)",
        "best_in": {
            "reddit": ["large subs (500k+ members)"],
            "twitter": ["viral threads", "tech complaints", "industry snark"],
            "linkedin": [],  # never on LinkedIn
        },
        "note": "NEVER in small/serious subs like r/vipassana. NEVER on LinkedIn.",
    },
}

# Posting pipelines (no recommendation style)
VALID_STYLES = set(STYLES.keys())

# Reply/engagement pipelines add recommendation
REPLY_STYLES = VALID_STYLES | {"recommendation"}

# ── Platform-specific policy overlay ────────────────────────────────
#
# Tier assignment (dominant / secondary / rare) is DB-driven — see
# get_dynamic_tiers() below. This dict only stores static policy that
# is not a performance judgment:
#   - `never`: tone/brand constraints (e.g. no snark on LinkedIn). Even
#     if the data showed high upvotes, we still do not want this style.
#   - `note`: per-platform tone/length hint shown at the top of the
#     styles prompt.

PLATFORM_POLICY = {
    "reddit": {
        "never": ["curious_probe"],
        "note": "Short wins. 1 punchy sentence or 4-5 of real substance. Start with 'I' or 'my'. Match style to subreddit culture.",
    },
    "twitter": {
        "never": [],
        "note": "Brevity wins. Direct product mentions OK (unlike Reddit). 1-2 sentences max.",
    },
    "linkedin": {
        "never": ["snarky_oneliner"],
        "note": "Professional but human. Softer critic framing. No snark. 2-4 sentences.",
    },
    "github": {
        "never": ["snarky_oneliner"],
        "note": "Technical and specific. Lead with the pain, then the fix. 400-600 chars.",
    },
    "moltbook": {
        "never": [],
        "note": "Agent voice ('my human'). Conversational but substantive. 2-4 sentences.",
    },
}

# Minimum sample size before we trust a style's avg_upvotes.
# Below this, the style is "explore" and gets the STYLE_FLOOR_PCT only.
MIN_SAMPLE_SIZE = 5

# Target-distribution tuning knobs (used by compute_target_distribution).
# WEIGHT_EXPONENT > 1 sharpens the distribution toward the winner.
# STYLE_FLOOR_PCT guarantees every non-never style still gets tested.
# STYLE_CAP_PCT prevents a runaway winner from starving the rest.
WEIGHT_EXPONENT = 2.0
STYLE_FLOOR_PCT = 5.0
STYLE_CAP_PCT = 50.0


def _fetch_style_stats(platform):
    """Query posts table for avg_upvotes per engagement_style on this platform.

    Returns a dict: {style_name: {"n": int, "avg_up": float}}.
    Returns {} on any DB error (cold start / DB unavailable / psycopg2 missing).
    """
    try:
        import os
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import db as dbmod
        dbmod.load_env()
        conn = dbmod.get_conn()
        cur = conn.execute(
            "SELECT engagement_style, COUNT(*) AS n, "
            "AVG(COALESCE(upvotes,0))::float AS avg_up "
            "FROM posts "
            "WHERE status='active' AND engagement_style IS NOT NULL "
            "AND our_content IS NOT NULL AND LENGTH(our_content) >= 30 "
            "AND upvotes IS NOT NULL AND platform = %s "
            "GROUP BY engagement_style",
            [platform],
        )
        rows = cur.fetchall()
        conn.close()
        return {r[0]: {"n": int(r[1]), "avg_up": float(r[2])} for r in rows}
    except Exception:
        return {}


def get_dynamic_tiers(platform, context="posting"):
    """Rank styles for `platform` by avg_upvotes from the posts table.

    Returns (dominant, secondary, rare) tuple of style-name lists.

    Policy:
      - Styles in PLATFORM_POLICY[platform].never are excluded entirely.
      - Styles with N < MIN_SAMPLE_SIZE are placed in `secondary` (explore),
        regardless of their noisy avg_up.
      - Styles with N >= MIN_SAMPLE_SIZE are sorted by avg_up DESC and split:
          top third  -> dominant
          middle     -> secondary
          bottom third (or single worst) -> rare
      - Any style with zero samples (never logged yet) is added to
        `secondary` so the LLM still explores it.
      - Cold start (no data at all): every non-never style becomes secondary.
    """
    never = set(PLATFORM_POLICY.get(platform, {}).get("never", []))
    candidate_styles = [s for s in STYLES.keys() if s not in never]

    stats = _fetch_style_stats(platform)

    trusted = []  # (style, avg_up) with N >= MIN_SAMPLE_SIZE
    explore = []  # styles with N < MIN_SAMPLE_SIZE (incl. zero samples)

    for style in candidate_styles:
        s = stats.get(style)
        if s and s["n"] >= MIN_SAMPLE_SIZE:
            trusted.append((style, s["avg_up"]))
        else:
            explore.append(style)

    trusted.sort(key=lambda x: x[1], reverse=True)

    if not trusted:
        # Cold start: no trusted performance data for this platform yet.
        return [], explore, []

    # Split trusted into thirds. Small lists (1-2 items) go entirely to dominant.
    t = len(trusted)
    if t <= 2:
        dominant = [s for s, _ in trusted]
        rare = []
    else:
        third = max(1, t // 3)
        dominant = [s for s, _ in trusted[:third]]
        rare = [s for s, _ in trusted[-third:]]
    secondary = [s for s, _ in trusted if s not in dominant and s not in rare]
    secondary = secondary + explore  # untrusted styles always explore
    return dominant, secondary, rare


# ── Target distribution ─────────────────────────────────────────────

def _last_picks(platform, limit=10):
    """Return the last `limit` engagement_style picks on `platform`, newest first.

    Used by the prompt to show recent pick history so the LLM can cool off a
    style that's been over-used. Returns [] on any DB error.
    """
    try:
        import os
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import db as dbmod
        dbmod.load_env()
        conn = dbmod.get_conn()
        cur = conn.execute(
            "SELECT engagement_style FROM posts "
            "WHERE platform = %s AND engagement_style IS NOT NULL AND engagement_style != '' "
            "AND our_content IS NOT NULL AND LENGTH(our_content) >= 30 "
            "AND our_content <> '(mention - no original post)' "
            "ORDER BY posted_at DESC LIMIT %s",
            [platform, int(limit)],
        )
        rows = cur.fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def compute_target_distribution(platform, context="posting"):
    """Compute per-style target pick% using sharpened avg_upvotes with floor+cap.

    Returns a list of dicts sorted by target pct DESC:
        [{"style": name, "pct": float, "n": int, "avg_up": float, "trusted": bool}]

    Policy:
      - Styles in PLATFORM_POLICY[platform].never are excluded.
      - Styles with N >= MIN_SAMPLE_SIZE get weight = avg_up ** WEIGHT_EXPONENT.
      - Styles with N < MIN_SAMPLE_SIZE (incl. zero) get STYLE_FLOOR_PCT only
        so noisy small-n styles (e.g. n=1 with avg_up=14) don't dominate.
      - STYLE_FLOOR_PCT is applied to every remaining style so nothing hits 0%.
      - STYLE_CAP_PCT caps the top style; overflow redistributes pro-rata.
      - Cold start (no trusted data): equal share across non-never styles.
    """
    never = set(PLATFORM_POLICY.get(platform, {}).get("never", []))
    candidates = [s for s in STYLES.keys() if s not in never]
    stats = _fetch_style_stats(platform)

    rows = []
    trusted_total = 0.0
    for style in candidates:
        s = stats.get(style)
        n = int(s["n"]) if s else 0
        avg = float(s["avg_up"]) if s else 0.0
        trusted = s is not None and n >= MIN_SAMPLE_SIZE
        weight = (avg ** WEIGHT_EXPONENT) if trusted else 0.0
        if trusted:
            trusted_total += weight
        rows.append({"style": style, "n": n, "avg_up": avg, "trusted": trusted,
                     "weight": weight, "pct": 0.0})

    if not rows:
        return []

    # Cold start: no trusted data. Equal share across all non-never styles.
    if trusted_total <= 0:
        share = 100.0 / len(rows)
        for r in rows:
            r["pct"] = share
        rows.sort(key=lambda r: r["style"])
        return rows

    # Raw score%: weight / total. Explore styles stay at 0.
    for r in rows:
        r["pct"] = (r["weight"] / trusted_total) * 100.0 if r["trusted"] else 0.0

    # Apply floor: every style gets at least STYLE_FLOOR_PCT.
    # Redistribute remaining mass pro-rata among styles that were already above floor.
    below = [r for r in rows if r["pct"] < STYLE_FLOOR_PCT]
    above = [r for r in rows if r["pct"] >= STYLE_FLOOR_PCT]
    floored_total = STYLE_FLOOR_PCT * len(below)
    remaining = max(0.0, 100.0 - floored_total)
    above_sum = sum(r["pct"] for r in above) or 1.0
    for r in below:
        r["pct"] = STYLE_FLOOR_PCT
    for r in above:
        r["pct"] = (r["pct"] / above_sum) * remaining

    # Apply cap: top style can't exceed STYLE_CAP_PCT. Overflow redistributes
    # pro-rata among others (their current pct as the weight).
    rows.sort(key=lambda r: r["pct"], reverse=True)
    if rows and rows[0]["pct"] > STYLE_CAP_PCT:
        overflow = rows[0]["pct"] - STYLE_CAP_PCT
        rows[0]["pct"] = STYLE_CAP_PCT
        others = rows[1:]
        others_sum = sum(r["pct"] for r in others) or 1.0
        for r in others:
            r["pct"] += overflow * (r["pct"] / others_sum)

    return rows


# ── Prompt generators ───────────────────────────────────────────────

def get_styles_prompt(platform, context="posting"):
    """Generate the engagement styles prompt block for a given platform.

    Shows an explicit per-style target pick% (computed from live avg_upvotes
    via compute_target_distribution) plus the last 10 picks on this platform,
    so the LLM can preferentially pick styles that are under-used vs target.

    Replaces the old PRIMARY/SECONDARY/RARE buckets, which tended to make
    the LLM fixate on one "safe" style regardless of actual performance data.

    Args:
        platform: "reddit", "twitter", "linkedin", "github", "moltbook"
        context: "posting" (new posts) or "replying" (engagement replies)
    """
    policy = PLATFORM_POLICY.get(platform, PLATFORM_POLICY["reddit"])
    never_styles = set(policy.get("never", []))

    targets = compute_target_distribution(platform, context)
    targets = [t for t in targets if t["style"] not in never_styles]
    last_picks = _last_picks(platform, limit=10)

    recent_counts = {t["style"]: 0 for t in targets}
    for p in last_picks:
        if p in recent_counts:
            recent_counts[p] += 1
    pick_n = max(1, len(last_picks))
    under_represented = []
    over_represented = []
    for t in targets:
        recent_pct = (recent_counts[t["style"]] / pick_n) * 100.0
        if recent_pct < t["pct"] - 5.0:
            under_represented.append(t["style"])
        elif recent_pct > t["pct"] + 10.0:
            over_represented.append(t["style"])

    lines = []
    lines.append("## Engagement styles (pick the one that fits — and that we're under-using)")
    lines.append("")
    lines.append(f"Match your style to the conversation. {policy['note']}")
    lines.append("")
    lines.append(
        f"Target pick distribution on {platform} (derived from live avg_upvotes, "
        f"sharpened by exponent {WEIGHT_EXPONENT:g} so the winner gets most traffic; "
        f"{STYLE_FLOOR_PCT:g}% floor per style so every style keeps getting tested):"
    )
    lines.append("")
    for t in targets:
        if t["trusted"]:
            sample = f"avg_up {t['avg_up']:.2f} · n={t['n']}"
        else:
            sample = f"n={t['n']} (below trust threshold; floor only)"
        lines.append(f"- **{t['style']}**: {t['pct']:.0f}%  ({sample})")
    lines.append("")
    if last_picks:
        lines.append(f"Your last {len(last_picks)} picks on {platform} (newest first): {', '.join(last_picks)}")
    else:
        lines.append(f"Your last picks on {platform}: (none yet)")
    if under_represented:
        lines.append(f"Under-used vs target right now (lean toward these): {', '.join(under_represented)}")
    if over_represented:
        lines.append(f"Over-used vs target right now (lean away): {', '.join(over_represented)}")
    lines.append("")
    lines.append("Rules:")
    lines.append("- Prefer a style whose recent pick-rate is BELOW its target% unless another style clearly fits the thread better.")
    lines.append("- The top style is the winner to reach for, not the default — pick it when it fits, not by habit.")
    lines.append("- Every style in the list is allowed; there is no hard tier.")
    if never_styles:
        lines.append(f"- Never on {platform}: {', '.join(sorted(never_styles))}.")
    lines.append("")

    for t in targets:
        style = STYLES.get(t["style"])
        if not style:
            continue
        best = style["best_in"].get(platform, [])
        lines.append(f"**{t['style']}**: {style['description']}")
        lines.append(f'  "{style["example"]}"')
        if best:
            lines.append(f"  Best in: {', '.join(best)}.")
        if style.get("note"):
            lines.append(f"  {style['note']}")
        lines.append("")

    if context == "replying":
        lines.append("**recommendation**: Recommend a project from config casually. MAX 20% of replies.")
        lines.append("")

    lines.append('AVOID the "pleaser/validator" style ("this is great", "had similar results", "100% agree"). It consistently gets the lowest engagement across all platforms.')
    return "\n".join(lines)


def get_content_rules(platform):
    """Generate platform-specific content rules.

    Args:
        platform: "reddit", "twitter", or "linkedin"

    Returns:
        Multi-line string of content rules.
    """
    common = [
        "NO em dashes. Use commas, periods, or regular dashes (-).",
        "Never say 'I built' or 'we built'. Never mention any project by name unless recommending.",
        'Never start with "exactly", "yeah totally", "100%", "that\'s smart".',
        "Specificity is the #1 authenticity signal. Use concrete numbers, dates, timeframes.",
        "Include imperfections: contractions, casual asides, occasional lowercase.",
    ]

    platform_rules = {
        "reddit": [
            "Go BIMODAL: either 1 punchy sentence (<100 chars, highest avg upvotes) or 4-5 sentences of real substance. AVOID the 2-3 sentence dead zone.",
            "Start with 'I' or 'my' (first-person experience). 'I did X' beats 'you should do X'.",
            "No markdown in Reddit (no ##, **, numbered lists). Casual tone, lowercase OK, fragments OK.",
            "NEVER mention product names (fazm, assrt, pieline, cyrano, terminator, mk0r, s4l). NEVER include URLs or links.",
            "Statements beat questions. Be authoritative, not inquisitive. No 'anyone else experience this?'",
        ],
        "twitter": [
            "Keep it short: 1-2 sentences max. Fragments and lowercase OK.",
            "Direct product mentions OK when relevant (unlike Reddit).",
            "No hashtags. No threads. No 'RT if you agree' bait.",
            "Punch line first, context second.",
        ],
        "linkedin": [
            "Professional but casual tone. 2-4 sentences.",
            "Softer framing for critic style (constructive, not combative).",
            "No snark. No sarcasm. Earnest insights land better here.",
            "Line breaks between thoughts for readability.",
        ],
    }

    rules = platform_rules.get(platform, platform_rules["reddit"]) + common
    return "\n".join(f"- {r}" for r in rules)


def get_anti_patterns():
    """Content anti-patterns shared across all platforms."""
    return """## Anti-patterns
- NEVER start with "exactly", "yeah totally", "100%", "that's smart". Vary first words.
- NEVER say "I built" / "we built" / "I'm working on". Frame products as recommendations, not self-promotion.
- NEVER suggest calls, meetings, demos.
- NEVER promise to share links/files not in config.json.
- NEVER offer to DM. NEVER make time-bound promises.
- Some replies should be 1 sentence. Not everything needs 3-4 sentences."""


def get_valid_styles(context="posting"):
    """Return the set of valid style names.

    Args:
        context: "posting" for new posts, "replying" for engagement replies.
    """
    if context == "replying":
        return REPLY_STYLES
    return VALID_STYLES


def validate_style(style, context="posting"):
    """Check if a style name is valid. Returns the style or None."""
    valid = get_valid_styles(context)
    if style and style in valid:
        return style
    return None
