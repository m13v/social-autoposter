#!/usr/bin/env python3
"""
Generate keyword candidates for any product using DataForSEO API.

Usage:
    python3 generate_keywords.py <product_name>
    python3 generate_keywords.py assrt
    python3 generate_keywords.py fazm

Uses two DataForSEO endpoints:
  1. keyword_suggestions: expand seed keywords from config.json topics
  2. keywords_for_site: steal competitor keywords

Filters by volume >= 20 and competition LOW/MEDIUM.
Merges into seo/state/<product>/underserved_keywords.json without
overwriting scored/done entries.
"""

import json
import os
import sys
import urllib.request
import base64
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
CONFIG_PATH = ROOT_DIR / "config.json"
ENV_PATH = ROOT_DIR / ".env"

MIN_VOLUME = 20
MAX_COMPETITION = {"LOW", "MEDIUM", None, "N/A"}

# Keywords containing these terms are likely irrelevant (e.g. theater playwrights)
NOISE_PATTERNS = {
    "oscar wilde", "sam shepard", "arthur miller", "wallace shawn",
    "mamet", "tennessee williams", "eugene o'neill", "beckett",
    "chekhov", "ibsen", "moliere", "shakespeare", "broadway",
    "theater", "theatre", "dramatis", "stage play",
    "miller playwright", "playwright playwright",
}


def load_env():
    """Load .env file into os.environ."""
    if ENV_PATH.exists():
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key.strip(), val.strip())


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def find_project(config, name):
    name_lower = name.lower()
    for p in config.get("projects", []):
        if p["name"].lower() == name_lower:
            return p
    return None


def dataforseo_request(endpoint, payload):
    """Make a DataForSEO API request."""
    login = os.environ.get("DATAFORSEO_LOGIN", "")
    password = os.environ.get("DATAFORSEO_PASSWORD", "")
    if not login or not password:
        print("ERROR: DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD must be set in .env")
        sys.exit(1)

    url = f"https://api.dataforseo.com/v3/{endpoint}"
    auth = base64.b64encode(f"{login}:{password}".encode()).decode()
    data = json.dumps(payload).encode()

    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Basic {auth}")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            if result.get("status_code") != 20000:
                print(f"  API error: {result.get('status_message')}")
                return None
            cost = result.get("cost", 0)
            if cost:
                print(f"  API cost: ${cost:.4f}")
            return result
    except Exception as e:
        print(f"  API request failed: {e}")
        return None


def fetch_keyword_suggestions(seed_keyword, limit=50):
    """Get keyword suggestions from DataForSEO Labs."""
    result = dataforseo_request(
        "dataforseo_labs/google/keyword_suggestions/live",
        [{"keyword": seed_keyword, "location_code": 2840, "language_code": "en",
          "limit": limit, "include_seed_keyword": True}]
    )
    if not result:
        return []

    items = result["tasks"][0]["result"][0].get("items") or []
    keywords = []
    for item in items:
        ki = item.get("keyword_info", {})
        vol = ki.get("search_volume", 0) or 0
        comp = ki.get("competition_level")
        if vol >= MIN_VOLUME and comp in MAX_COMPETITION:
            keywords.append({
                "keyword": item["keyword"],
                "volume": vol,
                "competition": comp or "N/A",
                "source": f"suggestion:{seed_keyword}",
            })
    return keywords


def fetch_keywords_for_site(domain, limit=50):
    """Get keywords a competitor ranks for."""
    result = dataforseo_request(
        "dataforseo_labs/google/keywords_for_site/live",
        [{"target": domain, "location_code": 2840, "language_code": "en",
          "limit": limit}]
    )
    if not result:
        return []

    items = result["tasks"][0]["result"][0].get("items") or []
    keywords = []
    for item in items:
        ki = item.get("keyword_info", {})
        vol = ki.get("search_volume", 0) or 0
        comp = ki.get("competition_level")
        if vol >= MIN_VOLUME and comp in MAX_COMPETITION:
            keywords.append({
                "keyword": item["keyword"],
                "volume": vol,
                "competition": comp or "N/A",
                "source": f"competitor:{domain}",
            })
    return keywords


def extract_competitor_domains(project):
    """Extract competitor domains from config."""
    domains = []
    comp = project.get("competitive_positioning", {})
    for key, desc in comp.items():
        # Try to find domain-like strings in the description
        # Also use common mappings
        name = key.replace("vs_", "").replace("_", " ")
        # Known mappings
        domain_map = {
            "qa wolf": "qawolf.com",
            "momentic": "momentic.ai",
            "manual playwright": None,  # not a competitor site
            "verkada rhombus": "verkada.com",
            "hakimo": "hakimo.ai",
            "guard": None,
            "soundhound": "soundhound.com",
            "loman": "loman.ai",
            "conversenow": "conversenow.ai",
            "human staff": None,
            "per minute pricing": None,
        }
        domain = domain_map.get(name)
        if domain:
            domains.append(domain)
    return domains


def generate_keywords_dataforseo(project):
    """Generate keywords using DataForSEO API."""
    all_keywords = []

    # 1. Keyword suggestions from topics
    topics = project.get("topics", [])
    print(f"\n  Fetching suggestions for {len(topics)} topics...")
    for topic in topics:
        print(f"    Topic: {topic}")
        kws = fetch_keyword_suggestions(topic, limit=30)
        print(f"    -> {len(kws)} keywords (vol >= {MIN_VOLUME})")
        all_keywords.extend(kws)

    # 2. Competitor keyword stealing
    domains = extract_competitor_domains(project)
    if domains:
        print(f"\n  Stealing keywords from {len(domains)} competitors...")
        for domain in domains:
            print(f"    Competitor: {domain}")
            kws = fetch_keywords_for_site(domain, limit=30)
            print(f"    -> {len(kws)} keywords (vol >= {MIN_VOLUME})")
            all_keywords.extend(kws)

    # Deduplicate by keyword, keep highest volume, filter noise
    seen = {}
    for kw in all_keywords:
        key = kw["keyword"].lower()
        # Filter out noise (e.g. theater playwrights for "playwright" topic)
        if any(noise in key for noise in NOISE_PATTERNS):
            continue
        # Skip single-word keywords (too broad to rank for)
        if len(key.split()) < 2:
            continue
        if key not in seen or kw["volume"] > seen[key]["volume"]:
            seen[key] = kw
    unique = sorted(seen.values(), key=lambda x: x["volume"], reverse=True)

    return unique


def generate_keyword_templates(project):
    """Fallback: generate keywords from templates (no API needed)."""
    name = project["name"]
    name_lower = name.lower()
    topics = project.get("topics", [])
    features = project.get("features", [])
    competitive = project.get("competitive_positioning", {})

    keywords = []

    topic_prefixes = [
        "how to {topic}", "{topic} guide", "{topic} best practices",
        "{topic} tutorial", "automated {topic}", "ai {topic}",
        "best {topic} tools", "{topic} framework",
    ]
    for topic in topics:
        for template in topic_prefixes:
            kw = template.format(topic=topic.lower())
            slug = kw.replace(" ", "-").replace(".", "-")
            keywords.append({"keyword": kw, "slug": slug, "source": "topic_template"})

    for comp_key in competitive:
        comp_name = comp_key.replace("vs_", "").replace("_", " ")
        for kw in [f"{comp_name} alternative", f"{comp_name} vs {name_lower}",
                    f"{name_lower} vs {comp_name}", f"best {comp_name} alternative free",
                    f"{comp_name} alternative open source"]:
            slug = kw.replace(" ", "-").replace(".", "-")
            keywords.append({"keyword": kw, "slug": slug, "source": "competitor"})

    seen = set()
    unique = []
    for kw in keywords:
        if kw["keyword"] not in seen:
            seen.add(kw["keyword"])
            unique.append(kw)
    return unique


def load_state(product_name):
    state_path = SCRIPT_DIR / "state" / product_name.lower() / "underserved_keywords.json"
    if state_path.exists():
        with open(state_path) as f:
            return json.load(f)
    return {
        "updated_at": None,
        "product": product_name,
        "description": f"Tracks underserved keyword candidates for {product_name} SEO pages.",
        "keywords": [],
    }


def save_state(product_name, state):
    state_dir = SCRIPT_DIR / "state" / product_name.lower()
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "underserved_keywords.json"
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)
    return state_path


def merge_keywords(state, new_candidates):
    """Merge new candidates into state without overwriting existing entries."""
    existing = {kw["keyword"].lower(): kw for kw in state["keywords"]}
    added = 0

    for candidate in new_candidates:
        key = candidate["keyword"].lower()
        if key not in existing:
            slug = candidate.get("slug") or candidate["keyword"].lower().replace(" ", "-").replace(".", "-")
            existing[key] = {
                "keyword": candidate["keyword"],
                "slug": slug,
                "source": candidate.get("source", "dataforseo"),
                "volume": candidate.get("volume"),
                "competition": candidate.get("competition"),
                "score": None,
                "signal1": None,
                "signal2": None,
                "signal3": None,
                "status": "unscored",
                "page_url": None,
                "scored_at": None,
                "completed_at": None,
                "notes": "",
            }
            added += 1
        else:
            # Update volume/competition if we have newer data
            if candidate.get("volume") and (existing[key].get("volume") is None
                                            or candidate["volume"] > existing[key].get("volume", 0)):
                existing[key]["volume"] = candidate["volume"]
                existing[key]["competition"] = candidate.get("competition")

    state["keywords"] = list(existing.values())
    return added


def main():
    load_env()

    if len(sys.argv) < 2:
        print("Usage: python3 generate_keywords.py <product_name> [--fallback]")
        config = load_config()
        names = [p["name"] for p in config.get("projects", [])]
        print("Products:", ", ".join(names))
        sys.exit(1)

    product_name = sys.argv[1]
    use_fallback = "--fallback" in sys.argv

    config = load_config()
    project = find_project(config, product_name)

    if not project:
        print(f"Error: product '{product_name}' not found in config.json")
        sys.exit(1)

    print(f"Generating keywords for: {project['name']}")
    print(f"  Topics: {len(project.get('topics', []))}")
    print(f"  Competitors: {len(project.get('competitive_positioning', {}))}")

    if use_fallback or not os.environ.get("DATAFORSEO_LOGIN"):
        print("  Mode: template fallback (no DataForSEO API)")
        candidates = generate_keyword_templates(project)
    else:
        print("  Mode: DataForSEO API")
        candidates = generate_keywords_dataforseo(project)

    print(f"\n  Generated: {len(candidates)} candidates")

    state = load_state(product_name)
    existing_count = len(state["keywords"])
    added = merge_keywords(state, candidates)

    state_path = save_state(product_name, state)
    total = len(state["keywords"])

    print(f"  Existing: {existing_count}")
    print(f"  New added: {added}")
    print(f"  Total: {total}")
    print(f"  State: {state_path}")

    # Summary by status
    statuses = {}
    for kw in state["keywords"]:
        s = kw.get("status", "unscored")
        statuses[s] = statuses.get(s, 0) + 1
    print(f"  Status breakdown: {json.dumps(statuses)}")

    # Show top by volume
    by_vol = sorted([k for k in state["keywords"] if k.get("volume")],
                    key=lambda x: x.get("volume", 0), reverse=True)
    if by_vol:
        print(f"\n  Top keywords by volume:")
        for k in by_vol[:10]:
            print(f"    {k['volume']:>6} | {k['keyword']:50s} | {k.get('status','?')}")


if __name__ == "__main__":
    main()
