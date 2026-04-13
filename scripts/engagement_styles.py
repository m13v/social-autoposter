#!/usr/bin/env python3
"""Shared engagement style definitions for all platforms.

Centralizes style taxonomy, platform-specific guidance, content rules,
and prompt generation so every pipeline (post_reddit, engage_reddit,
run-twitter, run-linkedin, engage-twitter, engage-linkedin) references
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

# ── Platform-specific weighting ─────────────────────────────────────

PLATFORM_WEIGHTS = {
    "reddit": {
        "dominant": ["critic", "storyteller", "pattern_recognizer"],
        "secondary": ["curious_probe", "contrarian", "data_point_drop"],
        "rare": ["snarky_oneliner"],
        "never": [],
        "note": "Match style to subreddit culture. Varies heavily by sub.",
    },
    "twitter": {
        "dominant": ["snarky_oneliner", "critic", "data_point_drop"],
        "secondary": ["contrarian", "pattern_recognizer"],
        "rare": ["storyteller", "curious_probe"],
        "never": [],
        "note": "Brevity wins. Direct product mentions OK (unlike Reddit). 1-2 sentences max.",
    },
    "linkedin": {
        "dominant": ["storyteller", "pattern_recognizer", "critic"],
        "secondary": ["curious_probe", "data_point_drop", "contrarian"],
        "rare": [],
        "never": ["snarky_oneliner"],
        "note": "Professional but human. Softer critic framing. No snark. 2-4 sentences.",
    },
    "github_issues": {
        "dominant": ["critic", "pattern_recognizer", "data_point_drop"],
        "secondary": ["curious_probe", "storyteller"],
        "rare": ["contrarian"],
        "never": ["snarky_oneliner"],
        "note": "Technical and specific. Lead with the pain, then the fix. 400-600 chars.",
    },
    "moltbook": {
        "dominant": ["storyteller", "pattern_recognizer", "critic"],
        "secondary": ["curious_probe", "contrarian", "data_point_drop"],
        "rare": ["snarky_oneliner"],
        "never": [],
        "note": "Agent voice ('my human'). Conversational but substantive. 2-4 sentences.",
    },
}


# ── Prompt generators ───────────────────────────────────────────────

def get_styles_prompt(platform, context="posting"):
    """Generate the engagement styles prompt block for a given platform.

    Args:
        platform: "reddit", "twitter", or "linkedin"
        context: "posting" (new posts) or "replying" (engagement replies)

    Returns:
        Multi-line string to embed in a prompt.
    """
    weights = PLATFORM_WEIGHTS.get(platform, PLATFORM_WEIGHTS["reddit"])
    never_styles = set(weights.get("never", []))

    lines = []
    lines.append("## Engagement styles (CRITICAL: pick the best style for each thread/post)")
    lines.append("")
    lines.append(f"Match your style to the conversation. {weights['note']}")
    lines.append("")

    for name, style in STYLES.items():
        if name in never_styles:
            continue
        best = style["best_in"].get(platform, [])
        best_str = ", ".join(best) if best else ""
        lines.append(f'**{name}**: {style["description"]}')
        lines.append(f'  "{style["example"]}"')
        if best_str:
            lines.append(f"  Best in: {best_str}.")
        if style.get("note"):
            lines.append(f"  {style['note']}")
        lines.append("")

    # Add recommendation style for reply contexts
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
            "Casual tone, lowercase OK, fragments OK. 2-4 short sentences (1 sentence for snarky_oneliner).",
            "No markdown in Reddit (no ##, **, numbered lists).",
            "No product links. No product names. No tool recommendations (unless recommendation style).",
            "End with a genuine question when possible (drives reply chains).",
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
