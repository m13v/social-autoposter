---
name: social-autoposter
description: "Automate social media posting across Reddit, X/Twitter, LinkedIn, and Moltbook. Find threads, post comments, track engagement stats. Use when: 'post to social', 'social autoposter', 'find threads to comment on', 'audit social posts', 'update post stats', or after completing any task (mandatory per CLAUDE.md)."
---

# Social Autoposter Skill

Automates finding, posting, and tracking social media comments across Reddit, X/Twitter, and LinkedIn. Designed to run on a schedule (cron-style) or on-demand after completing tasks.

## Trigger phrases

- "post to social", "social autoposter", "find threads to comment on", "audit social posts", "update post stats"
- Also triggered automatically by CLAUDE.md "After Completing Any Task" workflow

## Prerequisites

- **Database**: `~/social-autoposter/social_posts.db` (SQLite, also symlinked at `~/.claude/social_posts.db`) with `posts`, `threads`, `our_posts`, `thread_comments` tables
- **Prompt DB**: `~/claude-prompt-db/prompts.db` for finding recent successful work
- **Browser**: Playwright MCP for visiting platforms and posting
- **Logged-in accounts**: Reddit (u/Deep_Ad1959), X (@m13v_), LinkedIn (Matthew Diakonov), Moltbook (matthew-autoposter, API key in `~/social-autoposter/.env`)

## Our Projects & Links

When posting, match threads to the project that's most relevant and include the appropriate link as a helpful resource.

| Project | What it does | Website | GitHub |
|---------|-------------|---------|--------|
| S4L | Social media autoposter (this tool) | https://s4l.ai | https://github.com/m13v/social-autoposter |
| OMI | Open-source AI wearable / desktop app | https://omi.me | https://github.com/BasedHardware/omi |
| macOS MCP | MCP server for macOS automation | — | https://github.com/mediar-ai/mcp-server-macos-use |

Prefer website links when one exists (drives signups). Use GitHub for open source tools without a website.

## Database Schema Reference

The `posts` table tracks everything we post:

```
id, platform, thread_url, thread_author, thread_author_handle,
thread_title, thread_content, thread_engagement,
our_url, our_content, our_account,
posted_at, discovered_at,
status ('active'|'inactive'|'deleted'|'removed'),
status_checked_at, engagement_updated_at,
upvotes, comments_count, views,
source_turn_id, source_summary
```

---

## Workflow 1: Find Postable Content

Use this to discover what recent work is worth posting about.

### Steps

1. **Query prompt-db for recent successful turns:**
   ```sql
   SELECT id, timestamp, summary, tags, specificity_score
   FROM turns
   WHERE tags LIKE '%success%'
     AND (tags LIKE '%feature%' OR tags LIKE '%deployment%' OR tags LIKE '%bug_fix%' OR tags LIKE '%security%')
     AND specificity_score >= 3
     AND timestamp >= datetime('now', '-24 hours')
   ORDER BY specificity_score DESC, timestamp DESC
   ```

2. **Cross-reference against already-posted content:**
   ```sql
   SELECT source_turn_id, source_summary FROM posts
   WHERE source_turn_id IS NOT NULL
   ```
   Skip any turn IDs already in the posts table. Also do fuzzy matching on `source_summary` to avoid duplicates with different turn IDs.

3. **Rank candidates by postability:**
   - Humor potential (funny edge cases, unexpected behaviors, relatable dev pain)
   - Relatability (common problems other devs face)
   - Novelty (something genuinely new or surprising)
   - Thread fit (is there an active thread where this fits naturally?)

4. **Apply the 60/30/10 content mix:**
   - 60% humor: Make people laugh. Self-deprecating dev stories, funny bugs, unexpected outcomes
   - 30% inspirational: Cool technical achievements, elegant solutions, "look what's possible"
   - 10% promotional: Direct mentions of o6w.ai or products (only when it fits naturally)

5. **Output a ranked list** of candidates with suggested tone for each.

### Fallback: No New Work? Find Threads Where Our Projects Help

If no new candidates from prompt-db, search for threads where someone has a problem one of our projects solves — and share it as a helpful resource.

1. **Rate limit check first:**
   ```sql
   SELECT COUNT(*) FROM posts WHERE posted_at >= datetime('now', '-24 hours')
   ```
   If 4+ posts in the last 24 hours, **stop**. Max 4 posts per day.

2. **Search for threads that match our projects.** For each project in the "Our Projects & Links" table, search for threads where someone is asking about or struggling with the problem that project solves:
   - **S4L**: social media automation, Reddit engagement bots, auto-posting, finding threads to comment on
   - **OMI**: AI wearables, desktop AI apps, Swift/Flutter dev, always-on AI companion
   - **macOS MCP**: macOS automation, controlling Mac apps programmatically, AI agents on macOS
   - Search across subreddits, X, LinkedIn for recent posts matching these topics.

3. **Only comment when our project genuinely helps.** The thread must describe a problem our tool actually solves. Don't shoehorn a link where it doesn't fit. Share the link as a resource, not a pitch.

4. **Cross-check against existing posts** to avoid duplicates:
   ```sql
   SELECT thread_url FROM posts WHERE platform = '{platform}'
   ```

5. **Check our last 5 comments for repetition:**
   ```sql
   SELECT our_content FROM posts ORDER BY id DESC LIMIT 5
   ```
   Do NOT repeat the same talking points. Vary the content.

6. **If no thread fits naturally, stop.** Better to skip a run than force a bad comment.

7. **Log with `source_summary = 'fallback: [project_name] - [topic]'`** so fallback posts can be tracked separately.

---

## Workflow 2: Post to Platforms

Use this after finding candidates (Workflow 1) or when manually posting about completed work.

### Steps

1. **Check the database first** to avoid duplicate threads:
   ```sql
   SELECT url FROM threads WHERE platform = '{platform}'
   SELECT thread_url FROM posts WHERE platform = '{platform}'
   ```

2. **Search for threads where our project is a helpful resource.** Match the topic to a project from the "Our Projects & Links" table and search for threads where someone has the problem it solves:
   - **Reddit**: Search relevant subreddits for people asking about or struggling with the problem
   - **X/Twitter**: Search for tweets discussing the problem space
   - **LinkedIn**: Search for posts from professionals dealing with the problem

3. **Read the thread before commenting:**
   - Check thread tone (casual/technical/professional)
   - Read top comments for length and style cues
   - Note the thread age (don't comment on stale threads)

4. **Draft the comment:**
   - Match thread energy and length (2-3 sentences max, shorter if thread is casual)
   - Be authentic and value-adding, not spammy
   - Never list features. One key benefit relevant to the thread is enough
   - Apply the content mix principle (humor > inspiration > promotion)

5. **Post via Playwright MCP (with verification):**
   - Navigate to the thread URL
   - Find the reply/comment box
   - Type the comment text
   - Click the submit/reply button
   - **VERIFY the post went through:**
     - Wait 2-3 seconds after clicking submit
     - Take a snapshot of the page
     - Look for our comment text appearing in the thread (not just in the input box)
     - If the comment is still in the input box or a spinner is showing, wait and retry the submit click
     - If an error message appears (rate limit, "something went wrong", etc.), wait 10-30 seconds and retry
     - Retry up to 3 times before marking as failed
   - **Capture the URL of our posted comment:**
     - On Reddit: look for the permalink of our new comment
     - On X: the page URL after successful reply, or find our reply in the thread
     - On LinkedIn: no stable URL available, note as posted
   - If verification fails after retries, log the attempt with `status='failed'` and move to the next platform
   - **CLOSE THE TAB when done** — after capturing the URL and verifying, you MUST call `browser_tabs` with `action: "close"` to close the tab. Do NOT use `browser_close` (it doesn't actually close the tab). Do NOT navigate back, do NOT leave tabs open. Call `browser_tabs close` after EVERY page visit — audits, searches, and posts. Before opening any new page, close the current one first. At the end of the entire run, call `browser_tabs close` one final time.

6. **Log to database:**
   ```sql
   INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
     thread_title, thread_content, our_url, our_content, our_account,
     source_turn_id, source_summary, status)
   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active');
   ```
   Also insert into `threads` and `our_posts` tables for backward compatibility.

7. **Sync to remote database** after finishing a posting session (if configured):
   ```bash
   bash ~/social-autoposter/syncfield.sh
   ```
   This ensures posts made via Playwright (outside shell scripts) are reflected in the production database.

8. **Report back** with: what was posted, where (URLs), on which platforms.

### Platform-specific notes

**Reddit** (u/Deep_Ad1959, logged in via Google with matt@mediar.ai):
- Use old.reddit.com for more reliable automation
- Comment box is usually a textarea with class `usertext-edit`
- Good subreddits: r/openclaw, r/ClaudeAI, r/aiagents, r/devops, r/macapps, r/SaaS

**X/Twitter** (@m13v_):
- Reply to existing tweets in relevant conversations
- Keep replies concise (1-2 sentences ideal)
- Use the reply box under the tweet

**LinkedIn** (Matthew Diakonov):
- Comment on posts from relevant professionals
- More professional tone, but still brief
- LinkedIn comments don't have stable URLs, so `our_url` may be null

**Moltbook** (matthew-autoposter, API-based — no Playwright needed):
- Reddit-style social network for AI agents. Uses pure REST API, not browser automation.
- API base: `https://www.moltbook.com/api/v1`
- Auth: `Authorization: Bearer $MOLTBOOK_API_KEY` (loaded from `~/social-autoposter/.env`)
- **Create post**: `POST /api/v1/posts` with JSON body `{"title": "...", "content": "...", "type": "text", "submolt_name": "general"}`
- **Create comment**: `POST /api/v1/posts/{post_id}/comments` with JSON body `{"content": "..."}`
- **List posts**: `GET /api/v1/posts?limit=10` (for browsing trending threads)
- **Get post**: `GET /api/v1/posts/{uuid}` (for verification and stats)
- **Verification**: After posting, fetch the post by UUID and confirm `verification_status` is `"verified"`. If `"pending"`, wait 5s and retry (up to 3 times).
- **Rate limit**: Max 1 Moltbook post per 30 minutes. Check: `SELECT COUNT(*) FROM posts WHERE platform='moltbook' AND posted_at >= datetime('now', '-30 minutes')`
- **Tone**: Write as an agent, not a human. Use "my human" instead of "I". First-person agent perspective. Example: "my human runs 5 agents in parallel" not "I run 5 agents in parallel".
- `our_url` format: `https://www.moltbook.com/post/{uuid}`

---

## Workflow 3: Audit & Update Stats

Use this to check if existing posts are still live and capture engagement metrics.

### Fast Method: `stats.sh` (Reddit + Moltbook, no browser needed)

For Reddit and Moltbook posts, use the lightweight bash script instead of Playwright:

```bash
bash ~/.claude/skills/social-autoposter/stats.sh          # full output
bash ~/.claude/skills/social-autoposter/stats.sh --quiet   # summary only
```

This script:
- **Reddit**: Fetches comment scores and thread stats via Reddit's public JSON API (no auth needed). Detects deleted/removed comments.
- **Moltbook**: Fetches post upvotes and comment counts via Moltbook REST API (uses `MOLTBOOK_API_KEY` from `.env`). Detects deleted posts via `is_deleted` field.
- Updates `upvotes`, `comments_count`, `thread_engagement`, `engagement_updated_at` in the DB
- Logs to `~/.claude/skills/social-autoposter/logs/stats-<timestamp>.log`
- Runs automatically every 6 hours via `com.m13v.social-stats` launchd agent

Use the Playwright-based audit below for X/Twitter posts (which require OAuth) or when you need to verify Reddit post visibility visually.

### Full Method: Playwright Browser Audit (all platforms)

#### Steps

1. **Query all posts with URLs:**
   ```sql
   SELECT id, platform, our_url, our_content, status, upvotes, views, comments_count,
          status_checked_at, engagement_updated_at
   FROM posts
   WHERE our_url IS NOT NULL
   ORDER BY posted_at DESC
   ```

2. **Visit each URL via Playwright:**
   - For X posts: Look for view count, likes, reposts, replies, bookmarks
   - For Reddit comments (use old.reddit.com): Look for point count and child comments
   - For LinkedIn: Skip if no URL

3. **Determine post status:**
   - `active`: Post/comment is visible and accessible
   - `deleted`: Returns 404 or "this tweet has been deleted"
   - `removed`: Visible on the page but marked as removed by moderator
   - `inactive`: Thread is locked or archived

4. **Update the database:**
   ```sql
   UPDATE posts SET
     status = ?,
     status_checked_at = datetime('now'),
     upvotes = ?,
     comments_count = ?,
     views = ?,
     engagement_updated_at = datetime('now')
   WHERE id = ?
   ```

5. **Report summary:**
   - Total posts checked
   - Posts by status (active/deleted/removed/inactive)
   - Top performing posts by engagement
   - Posts with declining engagement (may need follow-up)

---

## Workflow 4: Reply Engagement (`engage.sh`)

Discover replies to our posts and engage with them. Runs automatically every 2 hours via `com.m13v.social-engage` launchd agent.

```bash
bash ~/social-autoposter/skill/engage.sh
```

### How it works

- **Phase A** (Python, no LLM): Scans Reddit JSON API + Moltbook REST API for new replies to our comments. Inserts into `replies` table as `pending` or `skipped`.
- **Phase A.5** (Claude + Playwright): Scans X/Twitter `/notifications/mentions` for new replies. X has no public API, so this requires browser automation + LLM. Discovers new mentions, replies to substantive ones (max 5), skips light acknowledgments. Logs everything to `replies` table.
- **Phase B** (Claude + Playwright/API): Drafts and posts replies to pending Reddit/Moltbook replies from Phase A. Max 5 per run.
- **Phase C**: Git sync, syncfield, log cleanup.

### The `replies` table

Tracks all replies to our posts and our responses:
```
id, post_id, platform, their_comment_id, their_author, their_content,
their_comment_url, our_reply_id, our_reply_content, our_reply_url,
parent_reply_id, depth, status ('pending'|'replied'|'skipped'|'error'),
skip_reason, discovered_at, replied_at
```

### Skip reasons
- `too_short`: Reply is under 5 words (Reddit/Moltbook) or 1-2 word acknowledgments (X)
- `filtered_author`: AutoModerator, [deleted], or our own account
- `too_old`: Reply is older than 7 days
- `light_acknowledgment`: "Thanks", "Great", "Awesome" — not worth a reply
- `deleted`: Comment was deleted/removed

---

## Quick Reference: Content Rules

1. **Write like you're texting a coworker.** Lowercase is fine. Sentence fragments are fine. Never start with "Makes sense" or "The nuance here is" or "What everyone here is describing." If it sounds like a blog post, rewrite it.
2. **First person, specific.** "I run 5 agents in parallel and my API bill is becoming a second rent payment" beats "Token costs scale linearly with concurrent agents." Always say "I" not "you" or "one."
3. **Reply to top comments, not just OP.** Replying to a 200+ upvote comment puts you in front of more eyeballs and feels more natural than a buried top-level comment.
4. **Only comment when you have a real angle from Matthew's work.** Our experience: running 5 Claude Code agents in parallel on a Swift/Rust/Flutter desktop app (OMI), CLAUDE.md-driven spec workflow, Playwright MCP for browser automation, dealing with token costs and rate limits daily. If the thread doesn't connect to something specific we've done, skip it.
5. **No self-promotion unless it directly solves OP's problem.** No feature lists.
6. **Include the relevant project link as a helpful resource.** Match the comment to a project from the "Our Projects & Links" table and share the appropriate URL. Use the product website when one exists (drives signups), GitHub for open source tools without a website. The link should feel like you're sharing a resource that helps the reader, not pitching a product.
7. **Comment on existing threads**: Don't create new posts unless explicitly asked. (Exception: Moltbook — we create original posts there since we're an agent with our own account.)
8. **On Moltbook, write as an agent.** Use "my human" not "I". First-person agent perspective. "my human runs 5 agents in parallel and the API bill is becoming a second rent payment" not "I run 5 agents in parallel."
8. **Log everything**: Every thread discovered and every comment posted goes in the database.

### Bad vs Good examples

BAD: "Makes sense — Claude already tries to `| tail -n 50` on its own but by then the tokens are already in context. Intercepting at the proxy layer is the right call."
GOOD: "gonna try this — I run 5 agents in parallel and my API bill is becoming a second rent payment"

BAD: "What everyone here is describing is basically specification-driven development — write a detailed enough spec and Claude can one-shot the feature."
GOOD: "I spend more time writing CLAUDE.md specs than I ever spent writing code. the irony is I'm basically doing waterfall now and shipping faster than ever."

BAD: "The gap isn't the AI, it's that nobody wants to be the person who broke the sales pipeline by plugging in an agent that hallucinated a discount."
GOOD: "we let an agent loose on our deploy pipeline last week. it worked perfectly. nobody trusts it anyway."
