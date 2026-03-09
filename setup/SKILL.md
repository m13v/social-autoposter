---
name: social-autoposter-setup
description: "Set up social-autoposter for a new user. Interactive wizard that installs via npm, creates the database, configures accounts, verifies browser logins, and optionally sets up scheduled automation. Use when: 'set up social autoposter', 'install social autoposter', 'configure social posting'."
---

# Social Autoposter Setup

Interactive setup wizard for social-autoposter. Walk the user through configuration step by step.

## When to use

- First-time setup of social-autoposter
- Reconfiguring accounts or adding new platforms
- Troubleshooting a broken setup

## Prerequisites

- Node.js 16+ (for `npx`)
- `sqlite3` available on PATH
- Python 3.9+ for running helper scripts
- A browser automation tool (Playwright MCP, Selenium, etc.) for platform login verification

---

## Setup Flow

Run these steps in order. Ask the user for input at each step. Don't skip ahead.

### Step 1: Install via npm

Check if already installed:

```bash
ls ~/social-autoposter/schema.sql 2>/dev/null && echo "FOUND" || echo "NOT_FOUND"
```

If NOT_FOUND, install:
```bash
npx social-autoposter init
```

This copies all scripts, schema, skill files, and config templates to `~/social-autoposter/`. It also:
- Creates `config.json` from `config.example.json` (if missing)
- Creates `.env` from `.env.example` (if missing) — includes pre-filled Neon DATABASE_URL
- Creates `social_posts.db` from `schema.sql` (if missing)
- Symlinks `~/.claude/skills/social-autoposter` → `~/social-autoposter/skill`

To update scripts later without touching config/data:
```bash
npx social-autoposter update
```

Set `SKILL_DIR=~/social-autoposter` for the rest of this wizard.

### Step 2: Verify the database

```bash
sqlite3 "$SKILL_DIR/social_posts.db" "SELECT name FROM sqlite_master WHERE type='table';"
```

Expected tables: `posts`, `threads`, `our_posts`, `thread_comments`, `replies`.

If missing, create it:
```bash
sqlite3 "$SKILL_DIR/social_posts.db" < "$SKILL_DIR/schema.sql"
```

### Step 3: Configure accounts

`config.json` already exists at `$SKILL_DIR/config.json`. Edit it with the user's accounts.

Ask the user for each platform they want to use:

**Reddit:**
- "What's your Reddit username?" → set `accounts.reddit.username`
- Login method is always `browser` (Reddit has no public posting API)

**X/Twitter:**
- "What's your X handle?" → set `accounts.twitter.handle`
- Login method is always `browser`

**LinkedIn:**
- "What's your LinkedIn name?" → set `accounts.linkedin.name`
- Login method is always `browser`

**Moltbook** (optional):
- "Do you want to set up Moltbook? (y/n)"
- If yes: "What's your Moltbook username?" and "What's your Moltbook API key?"
- Edit `$SKILL_DIR/.env` and set `MOLTBOOK_API_KEY=<key>` (the file already exists from init)
- Set `accounts.moltbook.username` and `accounts.moltbook.api_key_env` in `config.json`

### Step 4: Configure content

Ask the user:

**Subreddits:**
- "Which subreddits do you want to post in? (comma-separated)"
- Default suggestion: `ClaudeAI, ClaudeCode, programming, webdev, devops`

**Content angle:**
- "Describe your unique experience/perspective in 1-2 sentences. This helps the agent write authentic comments from your point of view."
- Example: "Building a macOS desktop AI agent. Experience with Swift, Claude API, and browser automation."

**Projects** (optional):
- "Do you have open source projects or products to mention when relevant? (y/n)"
- If yes, for each project ask: name, description, website URL, GitHub URL, topic keywords
- Store in `config.json` under `projects` array

### Step 5: Verify browser logins

For each configured platform, verify the user is logged in:

**Reddit:**
- Navigate to `https://old.reddit.com` using browser automation
- Check if a username appears in the top-right (logged in) or a "login" link (not logged in)
- If not logged in: "Please log into Reddit in your browser, then say 'done'"
- Re-check after they confirm

**X/Twitter:**
- Navigate to `https://x.com/home`
- Check if the home timeline loads (logged in) or a login page appears
- Same flow if not logged in

**LinkedIn:**
- Navigate to `https://www.linkedin.com/feed/`
- Check if the feed loads or a login page appears

**Moltbook:**
- Source the env file and test the API key:
  ```bash
  source ~/social-autoposter/.env
  curl -s -H "Authorization: Bearer $MOLTBOOK_API_KEY" "https://www.moltbook.com/api/v1/posts?limit=1"
  ```
- Check for a successful response (not an auth error)

Report which platforms are ready and which need attention.

### Step 6: Test run (dry run)

Run the thread finder to verify everything works:
```bash
python3 "$SKILL_DIR/scripts/find_threads.py" --limit 3
```

Show the user the candidate threads found. Don't post anything — just verify the pipeline works.

Rate limit is 40 posts per 24 hours (enforced by the script).

### Step 7: Set up automation (optional)

Ask: "Do you want posts to run automatically on a schedule? (y/n)"

If yes, and on macOS:
- The launchd plists are already in `$SKILL_DIR/launchd/`
- Symlink into `~/Library/LaunchAgents/`:
  ```bash
  ln -sf "$SKILL_DIR/launchd/com.m13v.social-autoposter.plist" ~/Library/LaunchAgents/
  ln -sf "$SKILL_DIR/launchd/com.m13v.social-stats.plist" ~/Library/LaunchAgents/
  ln -sf "$SKILL_DIR/launchd/com.m13v.social-engage.plist" ~/Library/LaunchAgents/
  launchctl load ~/Library/LaunchAgents/com.m13v.social-autoposter.plist
  launchctl load ~/Library/LaunchAgents/com.m13v.social-stats.plist
  launchctl load ~/Library/LaunchAgents/com.m13v.social-engage.plist
  ```
- Schedule: posting runs hourly, stats every 6 hours, reply engagement every 2 hours

If yes, and on Linux:
- Generate crontab entries:
  ```
  0 * * * * cd ~/social-autoposter && bash skill/run.sh
  0 */6 * * * cd ~/social-autoposter && bash skill/stats.sh
  0 */2 * * * cd ~/social-autoposter && bash skill/engage.sh
  ```

If no: "You can run manually anytime with `/social-autoposter`"

### Step 8: Summary

Print a summary:
```
Social Autoposter Setup Complete

  Installed:   ~/social-autoposter  (v1.0.3 via npm)
  Database:    ~/social-autoposter/social_posts.db
  Config:      ~/social-autoposter/config.json
  Env:         ~/social-autoposter/.env
  Skill:       ~/.claude/skills/social-autoposter

  Platforms:
    Reddit:    u/USERNAME ✓
    X/Twitter: @HANDLE ✓
    LinkedIn:  NAME ✓
    Moltbook:  USERNAME ✓

  Rate limit:  40 posts per 24 hours
  Automation:  launchd (hourly post, 6h stats, 2h engage)

  Try it:      /social-autoposter
  Update:      npx social-autoposter update
```
