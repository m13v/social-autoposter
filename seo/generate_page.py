#!/usr/bin/env python3
"""
Unified SEO page generator.

Called by run_serp_pipeline.sh (discovery) and run_gsc_pipeline.sh (proven demand).
Also usable directly for manual/adhoc triggers. Future pipelines just call generate().

Design: no templates. Creative brief prompt + dynamic component discovery from the
target repo. Claude decides structure, angle, and content. The generator enforces
observability (stream-json tool capture) and verification (commit lands on
origin/main, live URL 200) before marking state done.

Usage:
    python3 generate_page.py --product Fazm --keyword "local ai agent" \\
        --slug local-ai-agent --trigger serp

    from generate_page import generate
    result = generate(product="Fazm", keyword="local ai agent",
                      slug="local-ai-agent", trigger="serp")
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
CONFIG_PATH = ROOT_DIR / "config.json"

# Load .env so DATABASE_URL is available when we import db_helpers
ENV_PATH = ROOT_DIR / ".env"
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(SCRIPT_DIR))
import db_helpers  # noqa: E402


CLAUDE_TIMEOUT_SECONDS = 1200  # 20 minutes, generous for research + generation


# Content-type routing. Each entry owns the route prefix, the candidate file
# paths Claude should write to, and the example directories the generator tells
# Claude to read for component-composition patterns. Adding a new type (e.g.
# "comparison", "integration") is a matter of adding a row here and, if the
# website repo has a shell component for it, teaching the prompt about it.
CONTENT_TYPES = {
    "guide": {
        "route_prefix": "/t/",
        "path_candidates": [
            "src/app/(content)/t/{slug}/page.tsx",
            "src/app/t/{slug}/page.tsx",
        ],
        "example_dirs": ["src/app/(content)/t/"],
        "description": "a keyword-targeted guide page",
    },
    "alternative": {
        "route_prefix": "/alternative/",
        "path_candidates": [
            "src/app/alternative/{slug}/page.tsx",
        ],
        "example_dirs": ["src/app/alternative/", "src/app/t/"],
        "description": "an alternative/comparison page against a competitor product",
    },
    "use_case": {
        "route_prefix": "/use-case/",
        "path_candidates": [
            "src/app/use-case/{slug}/page.tsx",
        ],
        "example_dirs": ["src/app/use-case/", "src/app/t/"],
        "description": "a use-case page describing one specific job the product does",
    },
}


_ALTERNATIVE_RE = re.compile(
    r"\b(vs|alternative|alternatives|replacement|replace|competitor|competitors)\b",
    re.IGNORECASE,
)


def classify_content_type(keyword: str) -> str:
    """Cheap regex classifier. Defaults to 'guide' (safe fallback, no shell).

    Conservative on purpose: misrouting a keyword to the wrong shell is worse
    than leaving it on the general /t/ guide path. Expand the patterns as we
    build more page-type shells in the website repo.
    """
    kw = (keyword or "").lower().strip()
    if _ALTERNATIVE_RE.search(kw):
        return "alternative"
    return "guide"


def load_product_config(product: str) -> dict:
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    lower = product.lower()
    for p in cfg.get("projects", []):
        if p["name"].lower() == lower:
            return p
    raise SystemExit(f"Product '{product}' not found in config.json")


def resolve_source_paths(product_cfg: dict) -> list[dict]:
    """Return list of {path, description} with paths expanded and existence-checked."""
    sources = product_cfg.get("landing_pages", {}).get("product_source", [])
    out = []
    for s in sources:
        raw = s.get("path", "")
        path = os.path.expanduser(raw)
        out.append({
            "path": path,
            "description": s.get("description", "").strip(),
            "exists": os.path.isdir(path),
        })
    return out


def format_source_block(sources: list[dict]) -> str:
    if not sources:
        return ("(no external product source configured for this product)\n"
                "Treat the website repo as the product surface. Read widely in it "
                "for landing copy, component implementations, fixtures, and data.")
    parts = []
    for s in sources:
        missing = "" if s["exists"] else " [MISSING ON DISK — do not try to read]"
        parts.append(f"- {s['path']}{missing}\n  {s['description']}")
    return "\n\n".join(parts)


def build_prompt(product: str, keyword: str, slug: str, trigger: str,
                 product_cfg: dict, source_block: str,
                 content_type: str = "guide") -> str:
    repo = os.path.expanduser(product_cfg.get("landing_pages", {}).get("repo", ""))
    website = (product_cfg.get("landing_pages", {}).get("base_url")
               or product_cfg.get("website", ""))
    differentiator = product_cfg.get("differentiator", "")

    trigger_context = {
        "serp": "This keyword came from SERP discovery. It has SERP gap and the product fits the commercial intent.",
        "gsc": "This query is already driving impressions to the site in Google Search Console. Real users are searching for this. Capture the demand.",
        "manual": "This is an adhoc trigger. Treat the keyword as worth building.",
    }.get(trigger, "")

    ct = CONTENT_TYPES.get(content_type, CONTENT_TYPES["guide"])
    route_prefix = ct["route_prefix"]
    primary_path = ct["path_candidates"][0].format(slug=slug)
    example_dirs_str = ", ".join(f"`{repo}/{d}`" for d in ct["example_dirs"])
    page_url = f"{website.rstrip('/')}{route_prefix}{slug}"

    type_context = {
        "guide": "This is a general guide/explainer page. You have the most creative freedom here — the angle, section shape, and length are all yours.",
        "alternative": f"This is an alternative/comparison page. Readers arrived by searching for a competitor product. Your job is to show them {product} is the better pick for the use case their keyword implies. Read an existing alternative page in `{repo}/src/app/alternative/` to see if a shell component exists (e.g. AlternativePageShell) — if it does, use it and emit only a typed data object. If no shell exists in this repo, compose raw sections using the trust-signal components below.",
        "use_case": f"This is a use-case page describing one concrete job {product} does. Readers want to know whether {product} can handle their specific workflow. Show them, with at least one anchor_fact drawn from real product source. If a UseCasePageShell exists in `{repo}/src/components/seo/`, prefer it; otherwise compose raw sections.",
    }.get(content_type, "")

    return f"""You are building one SEO page for {product}. You decide the angle and the content. Your one job is to find something real about the product that no competitor page mentions, and build a page around that.

CONTENT TYPE: {content_type} ({ct['description']})
{type_context}

KEYWORD: "{keyword}"
SLUG: "{slug}"
PRODUCT: {product}
WEBSITE: {website}
REPO (your current working directory): {repo}
DIFFERENTIATOR: {differentiator}

TRIGGER: {trigger}
{trigger_context}

## Step 1 — Find an angle no competitor has

Before you write anything, do research. Budget ~15 minutes for this step. It matters more than the writing.

1a. Read the product source.

{source_block}

These are not prompts to extract facts from. They are where the real implementation, real behavior, and real constraints live. Open files. Trace what happens when the product actually does the thing the keyword describes. You are looking for something specific a reader would not find anywhere else.

1b. Run scripts for real data.

If the product has a `scripts/` folder in any of the paths above, look there. That is where database queries, analytics pulls, and data exports live. Run what is available instead of trying to connect to databases directly. Real numbers from the product beat invented benchmarks.

1c. Check the SERP.

Use WebSearch for "{keyword}" and read the top 5 results. Note what they all cover. Note what they all miss. Your angle should be in the gap.

1d. Commit to an angle.

Pick ONE specific thing the product does that is not in the top SERP results. That thing is the spine of your page.

## Step 2 — Write the concept

Before writing any code, output this block (prose, not JSON, not a code fence):

CONCEPT
  angle: <one sentence describing the specific product behavior your page is built around>
  source: <the exact file path or script command you verified this from>
  anchor_fact: <one concrete, checkable thing — a file name, a number, a specific behavior — that makes the page uncopyable>
  serp_gap: <what the top 5 search results miss that your angle fills>

If you cannot fill in all four lines with specific non-generic answers, stop and do more research. Do not proceed to Step 3 with a generic concept.

## Step 3 — Pick your component palette

You are working in an existing website repo with a shared SEO component library (`@seo/components`). Import everything from `@seo/components`. If the repo also has local components (e.g. in `@/components/`), you may use those too.

### Available components

**Trust signals** (required, see below): Breadcrumbs, ArticleMeta, ProofBand, FaqSection, JSON-LD helpers (articleSchema, breadcrumbListSchema, faqPageSchema, howToSchema).

**Visual content** (pick at least 3 from this list for each page):
- `AnimatedCodeBlock` (code, language?, filename?, typingSpeed?) — syntax-highlighted code with typing animation. Use when showing real code, config, or CLI commands from the product.
- `TerminalOutput` (lines[], title?) — terminal session with command/output/success/error lines. Use when showing what happens when you run something.
- `FlowDiagram` (title, steps[]) — visual step-by-step flow with icons and arrows. Use when explaining a process or architecture.
- `SequenceDiagram` (title, actors[], messages[]) — SVG sequence diagram with lifelines. Use when showing how components communicate.
- `CodeComparison` (leftCode, rightCode, leftLabel, rightLabel, title?) — side-by-side before/after. Use when showing what the product replaces or simplifies.
- `AnimatedChecklist` (title, items[]) — animated checklist with checkmarks. Use for feature lists or requirement breakdowns.
- `AnimatedMetric` / `MetricsRow` (metrics[]) — animated number counters. Use when you have real metrics (boot time, build count, response time).
- `InlineTestimonial` (quote, name, role?, stars?) — testimonial card. Use when you can reference a real user or credible source.
- `ComparisonTable` (productName, competitorName, rows[]) — feature comparison grid. Use for versus/alternative pages.
- `ProofBanner` (quote, source?, metric) — compact proof with large metric. Use for a standout stat mid-page.
- `AnimatedSection` (delay?) — scroll-triggered fade-in wrapper. Use to stagger content reveals.

**Rich layout and animation components:**
- `BentoGrid` (cards[]) — bento grid layout with varying card sizes (1x1, 2x1, 1x2, 2x2). Cards have icon, title, description, optional accent color and custom content. Use for feature overviews, capability showcases, or "what you get" sections.
- `BeforeAfter` (before, after, title?) — animated tab toggle between "before" and "after" states. Each side has content text and highlight bullets (red X marks for before, green checks for after). Use for workflow comparisons, migration stories, or problem/solution sections.
- `AnimatedDemo` (title, steps[], code?) — animated product demo that auto-plays through steps. Shows a dark "screen" with step content, progress bar, and step indicators. Optional collapsible code panel showing "How to build this." Use for walkthroughs, feature demos, or "watch it work" sections.
- `GlowCard` (children) — card with mouse-tracking glow effect (like Linear/Stripe). Glow follows cursor on hover. Use as a wrapper around feature highlights or key content blocks for premium feel.
- `ParallaxSection` (children, background?, intensity?) — section with parallax scrolling effect. Background moves at a different speed than foreground content. Use for hero-style visual breaks between content sections.
- `StepTimeline` (title?, steps[]) — vertical timeline with animated line drawing and staggered step reveals. Each step has a numbered dot, title, description, and optional detail panel. Use for processes, setup guides, or "how it works" narratives.

**CTAs:**
- `InlineCta` (heading, body, linkText?, href?) — inline CTA block with PostHog tracking.
- `StickyBottomCta` (description, buttonLabel, href) — fixed bottom bar that appears on scroll. Use instead of (or alongside) InlineCta for variety.

### Differentiation rule

**Do NOT clone the structure of existing pages.** Read one existing page in {example_dirs_str} ONLY to understand the import syntax and color conventions. Do NOT copy its section ordering, component selection, or layout pattern.

Each page must feel editorially distinct. Pick visual components that match YOUR angle:
- A "how it works" angle might use FlowDiagram + StepTimeline + AnimatedDemo
- A "vs. competitors" angle might use BeforeAfter + ComparisonTable + MetricsRow + GlowCard
- A "deep dive" angle might use SequenceDiagram + AnimatedCodeBlock + BentoGrid
- A "getting started" angle might use AnimatedDemo + StepTimeline + TerminalOutput
- A "feature showcase" angle might use BentoGrid + GlowCard + ParallaxSection + MetricsRow

You must use at least 3 visual content components (not counting trust signals). Using only prose sections with no visual components is a failure.

### Color palette (mandatory)

bg-white base, text-zinc-900 for headings, text-zinc-500/text-gray-600 for secondary text. Accent colors: `from-cyan-500 to-teal-500` gradient for CTAs, `text-teal-600` for links, `bg-teal-50 text-teal-700` for badges/pills, `bg-teal-50 border-teal-200` for tinted boxes. NEVER use violet, indigo, or purple anywhere.

## Step 4 — Build the page

- Location: `{repo}/{primary_path}` (or match the convention you found in Step 3 if the repo uses a different path).
- **Structure is yours to invent.** Let the angle from Step 2 dictate everything: section count, section order, which visual components appear where, how the story unfolds. Do not follow a fixed outline. Do not replicate the skeleton of any existing page.
- Length: however long the angle deserves. Shorter and specific beats longer and generic. Do not pad.
- Style: no em dashes, no en dashes, anywhere. Plain direct prose. First person fine where natural.
- At least one section must surface the anchor_fact from your concept, with enough specificity that a reader could verify it (file name, command, number, behavior description). This is the uncopyable part of the page.
- Do not invent statistics. Do not fabricate quotes. If you use numbers, they come from something you read or ran.
- **Visual rhythm:** Alternate between prose sections and visual components. Never stack more than two consecutive prose-only sections without a visual break (diagram, code block, metrics row, comparison table, checklist, or terminal output).

### Required trust signals

Every page MUST include all of the following, but their PLACEMENT is flexible (not locked to a fixed position):

1. **`Breadcrumbs`** — near the top of the page.
2. **`ArticleMeta`** — near the top, after or alongside the title.
3. **`ProofBand`** — anywhere in the top third of the page.
4. **`FaqSection`** — anywhere in the bottom third (does not have to be the last section). At least 5 concrete, specific FAQs drawn from your research. Generic FAQs are worse than no FAQs.
5. **JSON-LD structured data** — `<script type="application/ld+json">` tag. Import `articleSchema`, `breadcrumbListSchema`, and `faqPageSchema` from `@seo/components`.

## Step 5 — Typecheck, commit, and deploy

- Run `npx tsc --noEmit` in the repo to confirm the page compiles cleanly. Fix any errors you introduced before committing. Do not commit a file that fails typecheck.
- Stage the new page file (and any new components you added).
- Commit on the current branch with a clear message naming the keyword.
- Push to origin main (or whatever the repo's main branch is).
- Build and deploy per the repo's conventions. If the repo uses Vercel, push alone may trigger deploy.
- Confirm the commit is on origin before reporting.

## Step 6 — Report back

Output your CONCEPT block from Step 2 in the conversation so it is captured in the log.

Then, as your FINAL message (nothing after it), output exactly one line of JSON with this shape:

{{"success": true, "page_url": "{page_url}", "slug": "{slug}", "commit_sha": "<7-char sha>", "concept_angle": "<one-line angle>"}}

If anything went wrong:

{{"success": false, "error": "<specific reason>", "slug": "{slug}"}}

Do not output any text after the final JSON line.
"""


def run_claude_stream(prompt: str, cwd: str, log_dir: Path, slug: str) -> dict:
    """
    Invoke claude -p with stream-json output. Capture every tool call to a jsonl file.
    Returns a dict: {exit_code, final_result_text, tool_summary, stream_log_path}.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    stream_log = log_dir / f"{ts}_{slug}_stream.jsonl"

    cmd = [
        "claude", "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
    ]

    tool_calls: list[dict] = []
    final_text = ""
    start = time.time()

    with open(stream_log, "w") as log_f:
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError:
            return {"exit_code": 127, "final_result_text": "",
                    "tool_summary": {}, "stream_log_path": str(stream_log),
                    "error": "claude CLI not found on PATH"}

        assert proc.stdout is not None
        while True:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            log_f.write(line)
            log_f.flush()

            if time.time() - start > CLAUDE_TIMEOUT_SECONDS:
                proc.kill()
                return {"exit_code": 124, "final_result_text": final_text,
                        "tool_summary": _summarize_tools(tool_calls),
                        "stream_log_path": str(stream_log),
                        "error": f"timeout after {CLAUDE_TIMEOUT_SECONDS}s"}

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("type") == "assistant":
                msg = event.get("message", {})
                for block in msg.get("content", []):
                    if block.get("type") == "tool_use":
                        tool_calls.append({
                            "name": block.get("name"),
                            "input": block.get("input", {}),
                        })
            elif event.get("type") == "result":
                final_text = event.get("result", "") or ""

        proc.wait()

    return {
        "exit_code": proc.returncode,
        "final_result_text": final_text,
        "tool_summary": _summarize_tools(tool_calls),
        "stream_log_path": str(stream_log),
    }


def _summarize_tools(calls: list[dict]) -> dict:
    """Count tool calls and flag whether product source was touched."""
    summary: dict = {"total": len(calls), "by_name": {}, "reads": [], "bash": []}
    for c in calls:
        name = c.get("name", "unknown")
        summary["by_name"][name] = summary["by_name"].get(name, 0) + 1
        inp = c.get("input", {}) or {}
        if name == "Read":
            summary["reads"].append(inp.get("file_path", ""))
        elif name == "Bash":
            summary["bash"].append(inp.get("command", "")[:200])
    return summary


def count_source_touches(tool_summary: dict, source_paths: list[str]) -> dict:
    """How many Read/Bash calls touched the product source paths."""
    touches = {p: {"reads": 0, "bash": 0} for p in source_paths}
    for read_path in tool_summary.get("reads", []):
        for sp in source_paths:
            if read_path.startswith(sp):
                touches[sp]["reads"] += 1
    for cmd in tool_summary.get("bash", []):
        for sp in source_paths:
            if sp in cmd:
                touches[sp]["bash"] += 1
    return touches


_FINAL_JSON_RE = re.compile(r"\{[^{}]*\"success\"[^{}]*\}")


def parse_final_json(text: str) -> dict | None:
    """Extract the final JSON status line from Claude's result text."""
    if not text:
        return None
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        m = _FINAL_JSON_RE.search(line)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    m = _FINAL_JSON_RE.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def parse_concept(text: str) -> dict:
    """Extract the CONCEPT block from Claude's output, best-effort."""
    if not text:
        return {}
    out = {}
    lines = text.splitlines()
    in_block = False
    for line in lines:
        if line.strip().startswith("CONCEPT"):
            in_block = True
            continue
        if in_block:
            stripped = line.strip()
            if not stripped:
                if any(out.values()):
                    break
                continue
            m = re.match(r"^([a-z_]+):\s*(.+)$", stripped)
            if m:
                out[m.group(1)] = m.group(2).strip()
            else:
                break
    return out


def verify_commit_landed(repo_path: str, expected_file: str) -> dict:
    """Check origin/main for the expected file. Returns {ok, commit_sha, error}."""
    try:
        subprocess.run(["git", "fetch", "origin"], cwd=repo_path,
                       check=True, capture_output=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": f"git fetch failed: {e}"}

    try:
        r = subprocess.run(
            ["git", "log", "origin/main", "-1", "--format=%h", "--", expected_file],
            cwd=repo_path, capture_output=True, text=True, check=True,
        )
        sha = r.stdout.strip()
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": f"git log failed: {e.stderr}"}

    if not sha:
        return {"ok": False, "error": f"no commit on origin/main touching {expected_file}"}
    return {"ok": True, "commit_sha": sha}


def save_concept_file(concepts_dir: Path, slug: str, product: str, keyword: str,
                      concept: dict, final_json: dict | None,
                      tool_summary: dict, touches: dict) -> Path:
    concepts_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = concepts_dir / f"{ts}_{slug}.md"
    body = [
        f"# {keyword}",
        "",
        f"- product: {product}",
        f"- slug: {slug}",
        f"- generated: {ts}Z",
        "",
        "## Concept",
    ]
    if concept:
        for k, v in concept.items():
            body.append(f"- **{k}**: {v}")
    else:
        body.append("(no concept parsed)")
    body += ["", "## Final JSON", "```json",
             json.dumps(final_json or {}, indent=2), "```", "",
             "## Tool summary", "```json",
             json.dumps({"total": tool_summary.get("total", 0),
                         "by_name": tool_summary.get("by_name", {}),
                         "source_touches": touches}, indent=2),
             "```"]
    out.write_text("\n".join(body))
    return out


def update_state(trigger: str, product: str, keyword: str, status: str,
                 page_url: str | None = None, notes: str | None = None,
                 slug: str | None = None,
                 content_type: str | None = None) -> None:
    """Dispatch state updates to the right table based on trigger."""
    if trigger == "serp":
        kwargs = {}
        if page_url is not None:
            kwargs["page_url"] = page_url
        if notes is not None:
            kwargs["notes"] = notes
        if content_type is not None:
            kwargs["content_type"] = content_type
        db_helpers.update_status(product, keyword, status, **kwargs)
    elif trigger == "gsc":
        conn = db_helpers.get_conn()
        cur = conn.cursor()
        sets = ["status = %s", "updated_at = NOW()"]
        vals: list = [status]
        if page_url is not None:
            sets.append("page_url = %s"); vals.append(page_url)
        if slug is not None:
            sets.append("page_slug = %s"); vals.append(slug)
        if notes is not None:
            sets.append("notes = %s"); vals.append(notes)
        if content_type is not None:
            sets.append("content_type = %s"); vals.append(content_type)
        if status == "done":
            sets.append("completed_at = NOW()")
        vals.extend([product, keyword])
        cur.execute(
            f"UPDATE gsc_queries SET {', '.join(sets)} WHERE product = %s AND query = %s",
            vals,
        )
        conn.commit()
        cur.close()
        conn.close()
    elif trigger == "manual":
        pass  # caller manages state


def generate(product: str, keyword: str, slug: str, trigger: str = "manual",
             content_type: str | None = None) -> dict:
    """
    Full generation lifecycle. Caller already marked the row in_progress.
    Returns a structured result; also updates state on success/failure.

    content_type: override classifier. If None, classify_content_type() runs.
    """
    if content_type is None:
        content_type = classify_content_type(keyword)
    if content_type not in CONTENT_TYPES:
        content_type = "guide"

    product_cfg = load_product_config(product)
    repo_path = os.path.expanduser(
        product_cfg.get("landing_pages", {}).get("repo", "")
    )
    if not repo_path or not os.path.isdir(repo_path):
        update_state(trigger, product, keyword, "pending",
                     notes="repo missing on disk", slug=slug,
                     content_type=content_type)
        return {"success": False, "error": f"repo not found: {repo_path}",
                "content_type": content_type}

    sources = resolve_source_paths(product_cfg)
    source_block = format_source_block(sources)
    prompt = build_prompt(product, keyword, slug, trigger, product_cfg,
                          source_block, content_type=content_type)

    log_dir = SCRIPT_DIR / "logs" / product.lower()
    concepts_dir = SCRIPT_DIR / "concepts" / product.lower()

    stream = run_claude_stream(prompt=prompt, cwd=repo_path,
                               log_dir=log_dir, slug=slug)

    final_json = parse_final_json(stream["final_result_text"])
    concept = parse_concept(stream["final_result_text"])

    source_paths = [s["path"] for s in sources if s["exists"]]
    touches = count_source_touches(stream["tool_summary"], source_paths)

    save_concept_file(concepts_dir, slug, product, keyword,
                      concept, final_json, stream["tool_summary"], touches)

    if stream.get("error"):
        update_state(trigger, product, keyword, "pending",
                     notes=stream["error"][:500], slug=slug,
                     content_type=content_type)
        return {"success": False, "error": stream["error"],
                "content_type": content_type,
                "stream_log": stream["stream_log_path"],
                "tool_summary": stream["tool_summary"]}

    if not final_json or not final_json.get("success"):
        err = (final_json or {}).get("error", "no final success JSON from claude")
        update_state(trigger, product, keyword, "pending",
                     notes=err[:500], slug=slug,
                     content_type=content_type)
        return {"success": False, "error": err,
                "content_type": content_type,
                "stream_log": stream["stream_log_path"],
                "tool_summary": stream["tool_summary"]}

    expected_file_candidates = [
        tmpl.format(slug=slug)
        for tmpl in CONTENT_TYPES[content_type]["path_candidates"]
    ]
    verify = {"ok": False, "error": "file candidates not checked"}
    for candidate in expected_file_candidates:
        v = verify_commit_landed(repo_path, candidate)
        if v["ok"]:
            verify = v
            verify["file"] = candidate
            break

    if not verify["ok"]:
        update_state(trigger, product, keyword, "pending",
                     notes=f"commit not on origin/main: {verify.get('error','')}"[:500],
                     slug=slug, content_type=content_type)
        return {"success": False, "error": verify.get("error"),
                "content_type": content_type,
                "stream_log": stream["stream_log_path"],
                "tool_summary": stream["tool_summary"]}

    page_url = final_json.get("page_url") or ""
    update_state(trigger, product, keyword, "done",
                 page_url=page_url, slug=slug,
                 content_type=content_type)

    return {
        "success": True,
        "page_url": page_url,
        "commit_sha": verify["commit_sha"],
        "content_type": content_type,
        "concept": concept,
        "tool_summary": stream["tool_summary"],
        "source_touches": touches,
        "stream_log": stream["stream_log_path"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", required=True)
    ap.add_argument("--keyword", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--trigger", choices=["serp", "gsc", "manual"], default="manual")
    ap.add_argument("--content-type", choices=list(CONTENT_TYPES.keys()), default=None,
                    help="Override the regex classifier. Default: auto-classify from keyword.")
    args = ap.parse_args()

    result = generate(product=args.product, keyword=args.keyword,
                      slug=args.slug, trigger=args.trigger,
                      content_type=args.content_type)
    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
