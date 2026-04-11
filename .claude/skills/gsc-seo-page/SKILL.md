---
name: gsc-seo-page
description: "End-to-end workflow for shipping one SEO blog post on any project website, targeting a Google Search Console query that already has impressions. Reads project config from social-autoposter config.json, pulls the next unprocessed query from the project's seo/inbox/state/gsc_queries.json, writes a rich content page, verifies the build, pushes, and marks the state file as done. Use when: 'create SEO page', 'next SEO page', 'GSC page for cyrano', 'SEO blog post for pieline', or when the inbox cron triggers."
user_invocable: true
---

# GSC SEO Page

Create one SEO page for a project website, targeting a query that Google Search Console already shows impressions for. Every page starts as real search intent from GSC. No guessing, no keyword-stuffing, no invented topics.

## Arguments

Either:
1. **Project name only** (e.g., `"fazm"`) to pick the top pending query automatically, or
2. **Project name + specific query** (e.g., `"cyrano" "apartment security camera installation guide"`) for manual mode.

If no project is specified, list projects that have `landing_pages.repo` configured and ask which one.

## Step 0: Resolve Project Config

Read `~/social-autoposter/config.json` and find the project by name. Extract:

| Field | Config path | Example |
|---|---|---|
| `DOMAIN` | `projects[].website` (strip `https://`) | `fazm.ai` |
| `REPO_DIR` | `projects[].landing_pages.repo` | `~/fazm-website` |
| `BASE_URL` | `projects[].landing_pages.base_url` or `projects[].website` | `https://fazm.ai` |
| `GITHUB_REPO` | `projects[].landing_pages.github_repo` | `m13v/fazm-website` |
| `PROJECT_NAME` | `projects[].name` | `Fazm` |
| `BRAND_TERMS` | derive from name, domain, contact name | `{"fazm", "fazm.ai"}` |

**Required:** The project must have a `landing_pages.repo` pointing to a local repo directory. If it doesn't, tell the user and stop.

## Step 1: Locate or Bootstrap the SEO Inbox

Check if the repo already has a GSC inbox:

```bash
ls $REPO_DIR/seo/inbox/state/gsc_queries.json 2>/dev/null
```

**If the state file exists:** load it and proceed.

**If not:** bootstrap the inbox structure:

1. Create `$REPO_DIR/seo/inbox/state/` directory
2. Look for an existing `fetch_queries.py` in the repo. If none exists, create one adapted from the template below, using the project's `DOMAIN` and `SITE_URL`.
3. Run `python3 $REPO_DIR/seo/inbox/fetch_queries.py` to populate the initial state file.
4. If the GSC API fails (no credentials, no access), tell the user and explain they need to set up GSC API access for this domain.

### fetch_queries.py template

Adapt from the Fazm reference at `~/fazm-website/seo/inbox/fetch_queries.py`. Key changes:
- Replace `DOMAIN` with the project's domain
- Replace `SITE_URL` with `sc-domain:{DOMAIN}`
- Replace `BRAND_TERMS` with project-specific terms
- Keep the same state file schema, merge logic, and sorting

## Step 2: Detect Content Format and Load UI Blueprint

**MANDATORY:** Before writing any page content, load the `seo-page-ui` skill at `~/social-autoposter/.claude/skills/seo-page-ui/SKILL.md`. This defines the exact 11-section structure, animated SVG patterns, comparison tables, FAQ accordions, JSON-LD blocks, and color palette that every SEO page must follow. Do not deviate from it.

Each project website may use a different content system. Detect which one:

```bash
# Check for MDX blog (Next.js content directory pattern)
ls $REPO_DIR/content/blog/*.mdx 2>/dev/null | head -3

# Check for app router blog pages
ls $REPO_DIR/src/app/blog/*/page.tsx 2>/dev/null | head -3

# Check for /t/ guide pages (assrt pattern)
ls $REPO_DIR/src/app/t/*/page.tsx 2>/dev/null | head -3

# Check for pages directory (older Next.js)
ls $REPO_DIR/pages/blog/*.tsx 2>/dev/null | head -3
```

Based on what exists, determine:

| Content system | Where to write | Format |
|---|---|---|
| MDX blog (`content/blog/`) | `content/blog/{slug}.mdx` | MDX with frontmatter |
| App router pages (`src/app/blog/[slug]/`) | Create new page component | TSX page |
| Guide pages (`src/app/t/[slug]/`) | `src/app/(main)/t/{slug}/page.tsx` | TSX page |
| None found | Ask user where content should go | Depends |

If the project has existing blog posts, **read one** to learn the exact frontmatter schema, component patterns, and footer conventions. Match them exactly.

## Step 3: Pick the Target Query

**Autonomous mode (no query argument):** Take the first entry where `status == "pending"` and `impressions >= 5`.

**Manual mode:** Find the specified query in the state file. If missing, run `fetch_queries.py` first.

### Claim the query

Set `status` to `"in_progress"` immediately, before writing anything. This prevents race conditions.

### Check for duplicates

```bash
# Search existing content for the topic
grep -r -l -i "{keyword}" $REPO_DIR/content/ $REPO_DIR/src/app/ 2>/dev/null | head -5
```

If a close match exists, either mark the query `skip` or extend the existing post.

### Pick a slug

- kebab-case, derived from the query
- strip stop words only if long
- max ~60 chars
- no trailing year unless the query contains one

## Step 4: Write the Content

**Follow the `seo-page-ui` blueprint exactly.** The UI skill defines the 11 required sections, their order, and the exact component patterns. This step adapts those patterns to the content format detected in Step 2.

### For TSX pages (app router)

Build the page as a single TSX file following all 11 sections from `seo-page-ui`:
1. Breadcrumbs, 2. H1 + Lede, 3. Hero Animated SVG, 4. Problem + Comparison Table,
5. Example Prompts (7), 6. Workflow Diagram + Steps, 7. Benefits (3 cards),
8. Real-World Scenario, 9. FAQ Accordions (4), 10. CTA, 11. Related Links (6)

Include all 4 JSON-LD blocks (WebPage, BreadcrumbList, HowTo, FAQPage).

### For MDX blog posts

Adapt the 11 sections into MDX format. The structure is the same but uses markdown syntax with inline JSX for SVGs, tables, and structured data. Use the project's existing MDX frontmatter schema (read an existing post to match it).

### Writing rules, rich media, and quality checklist

All defined in `seo-page-ui`. Follow its writing rules, color palette, rich media requirements, and quality checklist exactly. Do not reference nonexistent image/video files; inline SVG is the safe default.

## Step 6: Build Verification

```bash
cd $REPO_DIR
npx next build 2>&1 | tail -20
```

Must see `Compiled successfully`. If it fails with errors in files you didn't touch, wait 60 seconds and retry up to 3 times (other agents may be editing).

## Step 7: Commit and Push

```bash
cd $REPO_DIR
git add {content_file} seo/inbox/state/gsc_queries.json
git commit -m "$(cat <<'EOF'
Add SEO page for GSC query: {query}

Impressions: {N} (last 90d). Targets pending query from the GSC
inbox state file.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
EOF
)"
git push origin main
```

Verify deployment:

```bash
sleep 150 && curl -s -o /dev/null -w "%{http_code}\n" $BASE_URL/{content_path}/{slug}
```

## Step 8: Update State File

Mark the query as done (should already be in the same commit):

```python
for q in state["queries"]:
    if q["query"] == "{exact query text}":
        q["status"] = "done"
        q["page_slug"] = "{slug}"
        q["page_url"] = "{BASE_URL}/{content_path}/{slug}"
        q["completed_at"] = datetime.now().isoformat()
        break
```

## Step 9: Request Indexing (optional)

For early pipeline pages, use the `google-search-console` skill's URL Inspection flow. Otherwise, let Google find the page via the auto-updated sitemap (1-3 days).

## Hooking Into the Pipeline

### For existing project websites

If the project already has a `seo/inbox/run_pipeline.sh` and launchd plist, this skill is invoked automatically by the cron. No extra setup needed.

### For new project websites

To add automated GSC page generation to a project:

1. **Bootstrap the inbox** (this skill does it automatically on first run)
2. **Create `$REPO_DIR/seo/inbox/run_pipeline.sh`** adapted from the template:

```bash
#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
STATE_FILE="$SCRIPT_DIR/state/gsc_queries.json"
LOCK_FILE="$SCRIPT_DIR/.pipeline.lock"

# Lock check (30-min staleness)
if [ -f "$LOCK_FILE" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -f %m "$LOCK_FILE" 2>/dev/null || echo 0) ))
    if [ "$LOCK_AGE" -gt 1800 ]; then rm -f "$LOCK_FILE"
    else exit 0; fi
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

cd "$REPO_DIR"
python3 "$SCRIPT_DIR/fetch_queries.py"

NEXT_QUERY=$(python3 -c "
import json, sys
with open('$STATE_FILE') as f:
    state = json.load(f)
for q in state['queries']:
    if q['status'] == 'pending' and q['impressions'] >= 5:
        print(q['query']); sys.exit(0)
print('')
")

[ -z "$NEXT_QUERY" ] && exit 0

claude --print --dangerously-skip-permissions -p "
Use the gsc-seo-page skill from ~/social-autoposter/.claude/skills/gsc-seo-page/SKILL.md.
Project: {PROJECT_NAME}
Target query: $NEXT_QUERY
Execute the full pipeline autonomously. Do NOT post to social media.
"
```

3. **Create a launchd plist** at `~/Library/LaunchAgents/com.m13v.{project}-seo-inbox.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.m13v.{project}-seo-inbox</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>{REPO_DIR}/seo/inbox/run_pipeline.sh</string>
    </array>
    <key>StartInterval</key>
    <integer>600</integer>
    <key>StandardOutPath</key>
    <string>{REPO_DIR}/seo/inbox/logs/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>{REPO_DIR}/seo/inbox/logs/launchd.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
```

4. **Load it:**
```bash
launchctl load ~/Library/LaunchAgents/com.m13v.{project}-seo-inbox.plist
```

### Connecting to client-website skill

The `client-website` skill builds the website structure. This skill creates ongoing content. The connection point is:

1. `client-website` creates the site with blog/content infrastructure (routes, MDX renderer, sitemap)
2. After the site is live and indexed, GSC starts reporting queries
3. This skill picks up those queries and creates targeted pages
4. The social-autoposter main skill can then promote those pages on social platforms

## Anti-patterns

- Writing about topics GSC hasn't shown impressions for
- Thin content under 700 words (expand or mark `skip`)
- Stock photo heroes (inline SVG or nothing)
- Em dashes, en dashes, AI vocabulary
- Marking a query `done` before the page is live at HTTP 200
- Duplicating an existing post (always check first)
- Referencing image/video paths that don't exist
- Running two pipeline instances without claiming via `in_progress`
- Picking queries with `impressions < 5` without explicit override

## Eligible Projects

Projects with `landing_pages.repo` in config.json:

| Project | Domain | Repo |
|---|---|---|
| Fazm | fazm.ai | ~/fazm-website |
| Cyrano | apartment-security-cameras.com | ~/cyrano-security |
| Assrt | assrt.ai | ~/assrt-website |
| PieLine | aiphoneordering.com | ~/pieline-phones |

## Quick Reference

| What | Where |
|---|---|
| Social-autoposter config | `~/social-autoposter/config.json` |
| State file (per project) | `$REPO_DIR/seo/inbox/state/gsc_queries.json` |
| Fetch script (per project) | `$REPO_DIR/seo/inbox/fetch_queries.py` |
| Pipeline runner (per project) | `$REPO_DIR/seo/inbox/run_pipeline.sh` |
| Fazm reference implementation | `~/fazm-website/seo/inbox/` |
