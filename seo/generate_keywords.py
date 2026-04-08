#!/usr/bin/env python3
"""
Generate underserved keyword candidates for any product in config.json.

Usage:
    python3 generate_keywords.py <product_name>
    python3 generate_keywords.py assrt
    python3 generate_keywords.py fazm

Reads product config from social-autoposter/config.json, generates keyword
candidates based on the product's topics, features, and competitive positioning,
then writes them to seo/state/<product>/underserved_keywords.json (merging with
existing entries so we never lose scored/done keywords).
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
CONFIG_PATH = ROOT_DIR / "config.json"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def find_project(config, name):
    name_lower = name.lower()
    for p in config.get("projects", []):
        if p["name"].lower() == name_lower:
            return p
    return None


def generate_keyword_templates(project):
    """Generate keyword candidates from product config."""
    name = project["name"]
    name_lower = name.lower()
    topics = project.get("topics", [])
    features = project.get("features", [])
    description = project.get("description", "")
    competitive = project.get("competitive_positioning", {})

    keywords = []

    # Template 1: "how to [topic]" variants
    topic_prefixes = [
        "how to {topic}",
        "{topic} guide",
        "{topic} best practices",
        "{topic} tutorial",
        "{topic} for beginners",
        "automated {topic}",
        "ai {topic}",
        "{topic} tools comparison",
        "best {topic} tools",
        "{topic} framework",
    ]

    for topic in topics:
        for template in topic_prefixes:
            kw = template.format(topic=topic.lower())
            slug = kw.replace(" ", "-").replace(".", "-")
            keywords.append({
                "keyword": kw,
                "slug": slug,
                "source": "topic_template",
            })

    # Template 2: Feature-based keywords
    feature_templates = [
        "how to {feature}",
        "{feature} automation",
        "{feature} tool",
    ]

    for feature in features:
        # Extract the core concept (first ~4 words)
        core = " ".join(feature.lower().split()[:5])
        # Skip features that are too generic
        if len(core) < 10:
            continue
        for template in feature_templates:
            kw = template.format(feature=core)
            slug = kw.replace(" ", "-").replace(".", "-")
            keywords.append({
                "keyword": kw,
                "slug": slug,
                "source": "feature_template",
            })

    # Template 3: Competitor comparison keywords
    for comp_key, comp_desc in competitive.items():
        # Extract competitor name from key like "vs_qa_wolf"
        comp_name = comp_key.replace("vs_", "").replace("_", " ")
        comp_keywords = [
            f"{comp_name} alternative",
            f"{comp_name} vs {name_lower}",
            f"{name_lower} vs {comp_name}",
            f"best {comp_name} alternative free",
            f"{comp_name} alternative open source",
        ]
        for kw in comp_keywords:
            slug = kw.replace(" ", "-").replace(".", "-")
            keywords.append({
                "keyword": kw,
                "slug": slug,
                "source": "competitor",
            })

    # Deduplicate by keyword
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
    existing = {kw["keyword"]: kw for kw in state["keywords"]}
    added = 0

    for candidate in new_candidates:
        if candidate["keyword"] not in existing:
            existing[candidate["keyword"]] = {
                "keyword": candidate["keyword"],
                "slug": candidate["slug"],
                "source": candidate["source"],
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

    state["keywords"] = list(existing.values())
    return added


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate_keywords.py <product_name>")
        print("Products:", end=" ")
        config = load_config()
        names = [p["name"] for p in config.get("projects", [])]
        print(", ".join(names))
        sys.exit(1)

    product_name = sys.argv[1]
    config = load_config()
    project = find_project(config, product_name)

    if not project:
        print(f"Error: product '{product_name}' not found in config.json")
        sys.exit(1)

    print(f"Generating keywords for: {project['name']}")
    print(f"  Topics: {len(project.get('topics', []))}")
    print(f"  Features: {len(project.get('features', []))}")
    print(f"  Competitors: {len(project.get('competitive_positioning', {}))}")

    candidates = generate_keyword_templates(project)
    print(f"  Generated: {len(candidates)} candidates")

    state = load_state(product_name)
    existing_count = len(state["keywords"])
    added = merge_keywords(state, candidates)

    state_path = save_state(product_name, state)
    total = len(state["keywords"])

    print(f"  Existing: {existing_count}")
    print(f"  New: {added}")
    print(f"  Total: {total}")
    print(f"  State: {state_path}")

    # Summary by status
    statuses = {}
    for kw in state["keywords"]:
        s = kw.get("status", "unscored")
        statuses[s] = statuses.get(s, 0) + 1
    print(f"  Status breakdown: {json.dumps(statuses)}")


if __name__ == "__main__":
    main()
