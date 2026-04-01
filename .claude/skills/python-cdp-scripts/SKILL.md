# Python CDP Browser Scripts

Create Python functions that connect to running MCP browser agents via Chrome DevTools Protocol (CDP) and perform complete browser automation workflows. Replaces Claude browser MCP calls entirely — zero LLM tokens consumed.

## Usage
```
/python-cdp-scripts <platform> <action-description>
/python-cdp-scripts linkedin "scrape engagement stats for our comments"
/python-cdp-scripts linkedin "check if posts are deleted"
/python-cdp-scripts linkedin "read unread DM conversations"
```

## How It Works

Unlike browser-script (JS scripts run via Claude's browser_run_code tool calls), this approach:
- Python connects directly to the running MCP browser via CDP port
- Reuses the existing logged-in session (cookies, tabs)
- Returns structured JSON to stdout
- Called from shell scripts — Claude is never involved
- Zero LLM tokens consumed for the automation

## Architecture

Each platform has a single Python file (e.g., `scripts/linkedin_browser.py`) with:
1. `find_cdp_port()` — scans running Chrome/Chromium processes for remote-debugging-port flags
2. `get_browser_and_page()` — connects via CDP, reuses existing platform tab (critical: new pages don't inherit cookies)
3. Individual command functions (e.g., `search_posts()`, `discover_notifications()`, `scrape_stats()`)
4. CLI interface via `if __name__ == "__main__"` with subcommands

## Workflow

### Step 1: Identify the automation target
Look at the shell script (`skill/*.sh`) for steps that currently use `claude -p` with browser MCP calls purely for automation (no content decisions). These are candidates for Python CDP replacement.

### Step 2: Write the function

Add to the existing platform script (e.g., `scripts/linkedin_browser.py`):

```python
def new_function(param1, param2):
    """One-line description of what this does.
    
    Returns JSON: {"field": "value", ...}
    """
    from playwright.sync_api import sync_playwright
    
    with sync_playwright() as p:
        browser, page, is_cdp = get_browser_and_page(p)
        
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            
            # Use page.evaluate() for in-page JS
            result = page.evaluate("""() => {
                // DOM manipulation, internal API calls, etc.
                return { data: "value" };
            }""")
            
            return result
            
        finally:
            if not is_cdp:
                page.close()
                browser.close()
```

### Step 3: Add CLI subcommand

```python
# In main()
elif cmd == "new-command":
    result = new_function(sys.argv[2], sys.argv[3])
    print(json.dumps(result, indent=2))
```

### Step 4: Test standalone

```bash
python3 scripts/linkedin_browser.py new-command arg1 arg2
```

### Step 5: Update the shell script

Replace the `claude -p` block with direct Python call:

```bash
# Old: claude -p "Navigate to... run JS... extract..." (30-50K tokens)
# New:
RESULT=$(python3 "$REPO_DIR/scripts/linkedin_browser.py" new-command arg1 arg2)
# Process $RESULT with python3 -c or jq (0 tokens)
```

## Design Rules

1. **Reuse existing tabs** — CDP-connected browsers share cookies only with existing tabs. Creating `context.new_page()` opens a blank session. Always find and reuse the platform's existing tab.

2. **Prefer `page.evaluate()` for data extraction** — runs JS in-page, has access to cookies for internal API calls (Voyager API, etc.). Faster than DOM navigation.

3. **Use internal APIs when available** — LinkedIn's Voyager API (`/voyager/api/...`) provides structured data. Extract CSRF token from cookies, fetch with proper headers. More reliable than DOM scraping.

4. **Return structured JSON** — every function returns a dict/list that gets `json.dumps()`-ed to stdout. Shell scripts consume with `python3 -c "import json..."`.

5. **Handle CDP port discovery robustly** — multiple Chrome instances may be running. Scan all ports, check `/json` endpoint for pages, prefer ports with logged-in platform pages (URLs without `/login/` or `/uas/`).

6. **Generous timeouts** — social sites are slow. 3s after navigation, 2s after scroll, 1s after click. Use `wait_until="domcontentloaded"` not `"networkidle"` (can hang).

7. **Stderr for diagnostics, stdout for data** — print debug info to stderr, JSON output to stdout. Shell scripts capture with `cmd 2>/dev/null`.

8. **One function = one complete workflow** — "extract all notifications", "scrape stats for a URL", "check if post is deleted". Not individual clicks.

9. **Fallback gracefully** — if CDP connection fails or page structure changed, return `{ok: false, error: "reason"}` instead of crashing.

10. **No LLM decisions in Python** — the script does mechanical work only. Content decisions stay in Claude prompts.

## When NOT to Use

- **Content generation** — Claude needs to decide what to write
- **Complex multi-step decisions** — "if post is about X, do Y, otherwise Z" where X requires understanding
- **One-off debugging** — just use browser_snapshot manually
- **Actions that need Claude's judgment mid-flow** — use browser-script instead

The split: **Python CDP handles MECHANICAL work** (navigate, extract, scrape, post via API). **Claude handles JUDGMENT work** (pick posts, write comments, decide strategy).

## CDP Connection Details

### Finding the browser port

```python
def find_cdp_port():
    # Scan ps aux for --remote-debugging-port=NNNN
    # Check each port's /json endpoint for platform pages
    # Prefer ports with logged-in pages (feed/notifications, not login/uas)
```

### Reusing existing tabs (CRITICAL)

```python
def get_browser_and_page(playwright):
    browser = playwright.chromium.connect_over_cdp(f"http://localhost:{port}")
    context = browser.contexts[0]  # Reuse existing context
    # Find existing platform tab - DO NOT create new pages
    for pg in context.pages:
        if "linkedin.com" in pg.url and "/login" not in pg.url:
            return browser, pg, True  # is_cdp=True
```

New pages created via `context.new_page()` do NOT inherit cookies from the MCP browser session. This is the #1 gotcha.

## Platform Notes

### LinkedIn
- CDP port: found by scanning Chrome processes for `--remote-debugging-port`
- Prefer ports with logged-in pages (feed, notifications URLs, not login/uas)
- Voyager API: `/voyager/api/voyagerIdentityDashNotificationCards` for notifications
- Activity IDs: hidden in new React DOM, extract via control menu's Report link (`updateUrn` param)
- Comment identification: use `button[aria-label*="View more options for <name>"]` to find our comment container
- Old DOM selectors (`article.comments-comment-entity`) no longer work -- LinkedIn uses obfuscated CSS classes
- API for posting: `linkedin_api.py` handles comments, replies, likes via REST API
- Agent config: `~/.claude/browser-agent-configs/linkedin-agent.json`

### Reddit (future)
- old.reddit.com is simpler to automate
- Reddit API exists for most operations -- prefer API over browser
- Agent: reddit-agent

### Twitter/X (future)
- Twitter API handles most operations
- Browser needed for: reading DMs, visual verification
- Agent: twitter-agent

## Existing Functions

### linkedin_browser.py

| Command | Action | Input | Output |
|---------|--------|-------|--------|
| `notifications` | Extract notifications via Voyager API | none | `[{type, commentUrn, activityId, authorName, ...}]` |
| `search URL` | Search posts, extract activity IDs | search URL | `{activity_ids: [...], posts: [{activity_id, author, text}]}` |
| `comment-context URL` | Get comment thread for a post | post URL | `{activity_id, comments: [{author, content}]}` |
| `activity-id URL` | Extract activity ID from post | post URL | `{activity_id, post_text, author}` |
| `stats URL [PREFIX]` | Scrape reaction count on our comment | post URL + optional content prefix | `{found, reactions, comment_preview}` |
| `stats-batch JSON` | Batch stats for multiple posts | JSON array of `[{id, url, content_prefix}]` | `[{id, url, found, reactions, comment_preview}]` |
| `audit URL` | Check if post is live or deleted | post URL | `{status, reactions, comments, views}` |
| `audit-batch JSON` | Batch audit for multiple posts | JSON array of `[{id, url}]` | `[{id, url, status, reactions, comments, views}]` |

### linkedin_api.py (companion REST API wrapper)

| Command | Action | Input | Output |
|---------|--------|-------|--------|
| `comment ACTIVITY_ID TEXT` | Post comment | activity ID + text | `{ok, comment_urn, our_url}` |
| `reply ACTIVITY_ID PARENT_URN TEXT` | Reply to comment | activity ID + parent URN + text | `{ok, reply_urn, permalink}` |
| `post TEXT` | Create new post | text | `{ok, post_urn}` |
| `like ACTIVITY_ID` | Like a post | activity ID | `{ok}` |
| `delete POST_URN` | Delete a post | post URN | `{ok}` |
| `whoami` | Get authenticated user info | none | `{ok, name, email}` |

## vs browser-script Approach

| | Python CDP | browser-script (JS) |
|--|-----------|-------------------|
| Token cost | 0 | 2 tool calls (~2-5K tokens) |
| When to use | Shell script automation, no Claude in loop | Mid-Claude-session, Claude deciding content |
| Execution | `python3 script.py cmd args` | `browser_run_code` via MCP |
| Session | Connects to existing browser via CDP | Runs inside MCP browser agent |
| Best for | Stats, audit, notifications, search | Edit comment, post with dynamic text |
