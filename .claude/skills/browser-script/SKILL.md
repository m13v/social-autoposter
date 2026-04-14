---
name: browser-script
description: "Create a self-contained JS browser automation script that navigates pages, clicks elements, fills forms, and extracts data — bundled into a single browser_run_code call. Replaces 5-10 Claude browser tool calls with 2 calls, cutting token usage 10-20x. Use when: 'automate browser flow', 'create browser script', 'script this browser task', 'bundle browser steps', 'reduce browser tokens'."
user_invocable: true
---

# Browser Script Builder

Create self-contained JS scripts that run inside platform browser agents (linkedin-agent, reddit-agent, twitter-agent) via `browser_run_code`. Replaces 5-10 Claude browser tool calls with 2 calls (set params + run script), cutting token usage 10-20x.

## Usage

```
/browser-script <platform> <action-description>
/browser-script linkedin "edit a comment to append link text"
/browser-script reddit "post a reply to a comment"
/browser-script linkedin "extract post data from search results"
```

## Workflow

### Step 1: Understand the browser flow

Before writing code, manually walk through the flow in the target browser agent to discover:
- What elements exist (use `browser_snapshot`)
- What role/name locators work (use `getByRole`)
- What API calls the page makes (use `browser_network_requests`)
- What timing/scrolling is needed

### Step 2: Write the script

Create `scripts/<action>_<platform>.js` following this skeleton:

```javascript
// action_platform.js — One-line description
// Runs inside {platform}-agent browser via browser_run_code.
//
// Params via window.__params:
//   { field1: "...", field2: "..." }
//
// Returns: { ok: true, ...data } or { ok: false, error: "error_code" }

async (page) => {
  const params = await page.evaluate(() => window.__params);
  if (!params || !params.requiredField) {
    return JSON.stringify({ ok: false, error: 'missing_params' });
  }

  try {
    // Step 1: Navigate
    await page.goto(params.url, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(3000);

    // Step 2: Scroll to load content
    await page.evaluate(() => window.scrollBy(0, 1500));
    await page.waitForTimeout(2000);

    // Step 3: Find element (with retry after scroll)
    const element = page.getByRole('button', { name: /pattern/i }).first();
    try {
      await element.waitFor({ timeout: 10000 });
    } catch {
      await page.evaluate(() => window.scrollBy(0, 2000));
      await page.waitForTimeout(2000);
      try {
        await element.waitFor({ timeout: 5000 });
      } catch {
        return JSON.stringify({ ok: false, error: 'element_not_found' });
      }
    }

    // Step 4: Interact
    await element.click();
    await page.waitForTimeout(1000);

    // Step 5: Verify
    return JSON.stringify({ ok: true, result: 'done' });

  } catch (e) {
    return JSON.stringify({ ok: false, error: e.message });
  }
}
```

### Step 3: Test interactively

Test in two calls:

```
// Set params
mcp__{platform}-agent__browser_run_code code:
  async (page) => {
    await page.evaluate(() => {
      window.__params = { url: "...", text: "..." };
    });
  }

// Run script
mcp__{platform}-agent__browser_run_code filename:
  ~/social-autoposter/scripts/the_script.js
```

If it fails, take a `browser_snapshot` to debug, fix the script, re-test.

### Step 4: Update the shell script

Replace the Claude prompt instructions for this flow with the 2-call pattern. Example from engage.sh Phase D LinkedIn:

```bash
# Old: 8-line prompt telling Claude to navigate, find comment, click menu, edit, save
# New:
8. For LinkedIn: use the edit script via linkedin-agent browser:
   a. Set params: browser_run_code with code setting window.__params
   b. Run: browser_run_code with filename=$REPO_DIR/scripts/edit_linkedin_comment.js
   c. Parse JSON result: {ok:true} = success, {ok:false, error} = handle error
```

## Design Rules (MUST follow)

1. **Params via `window.__params`** — never hardcode. Script must be reusable across different targets.

2. **Return JSON always** — `{ok: true, ...data}` or `{ok: false, error: "code"}`. Error codes are specific and actionable: `comment_not_found`, `link_already_present`, `not_logged_in`, `save_failed`, `element_not_found`.

3. **Script owns all waits** — `waitForTimeout`, `waitFor`, scroll-retry loops. Caller just runs and reads result.

4. **`keyboard.type()` not `fill()`** — React-based sites (LinkedIn, possibly Twitter) don't detect `fill()` changes. Always `keyboard.type()` for rich text editors. For full text replacement: `click({clickCount: 3})` + `Meta+a` + `keyboard.type(newText)`.

5. **Scroll before find** — social platforms lazy-load. Always scroll into view before locating elements.

6. **Locate by role/name** — `page.getByRole('button', {name: /pattern/i})` not CSS class selectors. Survives redesigns.

7. **One script = one complete action** — "edit a comment", "scan notifications", "post a reply". Not a single click. Not an entire engagement loop.

8. **Safety checks inside the script** — dedup detection (link already present, already replied). Don't rely on the caller.

9. **Generous timeouts** — social sites are slow. Use 3s after navigation, 2s after scroll, 1s after click.

10. **Two-phase element finding** — first try with 10s timeout, then scroll more and retry with 5s. Return specific error if still not found.

## When NOT to Script

- **Content generation** — Claude needs to decide what to write. Keep that in prompts.
- **Project/topic matching** — Claude picks which project fits. Keep in prompts.
- **One-off investigations** — manual browser_snapshot is fine.
- **Flows that vary** — if the steps change based on page state in unpredictable ways.

The split: **Claude decides WHAT** (pick project, write text, choose targets). **Scripts execute HOW** (navigate, click, type, save).

## Platform Notes

### LinkedIn
- Rich text editor uses React — `fill()` won't trigger change detection
- Edit textbox is the LAST `textbox[name="Text editor for creating comment"]` after clicking Edit
- Our comment menu: `button[name=/View more options for Matthew/i]`
- Official API cannot read or edit comments
- Internal Voyager API accessible via in-page `fetch()` for read-only operations (notifications)
- Agent: `mcp__linkedin-agent__browser_run_code`

### Reddit
- old.reddit.com is simpler to automate than new Reddit
- Edit button visible on own comments without menu
- Agent: `mcp__reddit-agent__browser_run_code`

### Twitter/X
- Tweets cannot be edited
- Agent: `mcp__twitter-agent__browser_run_code`

## Existing Scripts

| Script | Platform | Action | Params |
|--------|----------|--------|--------|
| `scan_linkedin_notifications.js` | LinkedIn | Read notifications via internal API | none (uses cookies) |
| `edit_linkedin_comment.js` | LinkedIn | Append text to our comment | `{postUrl, appendText}` |

## Reference

Full design doc: `scripts/BROWSER_SCRIPTS.md`
