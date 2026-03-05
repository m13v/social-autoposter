---
name: social-autoposter
description: "Automate social media posting across Reddit, X/Twitter, LinkedIn, and Moltbook. Find threads, post comments, track engagement stats. Use when: 'post to social', 'social autoposter', 'find threads to comment on', 'audit social posts', 'update post stats'."
---

# Social Autoposter Skill

Automates finding, posting, and tracking social media comments across Reddit, X/Twitter, LinkedIn, and Moltbook. Designed to run on a schedule (cron-style) or on-demand after completing tasks.

## Setup

1. Copy `config.example.json` to `config.json` in your skill directory
2. Fill in your account handles, database paths, subreddits, and content angle
3. Create the SQLite database (see schema below)
4. Log into your social accounts in the browser (for Playwright MCP)
5. For Moltbook: set `MOLTBOOK_API_KEY` in your `.env` file

The skill reads `config.json` at runtime for all personal settings — accounts, paths, subreddits, and content angle. Never hardcode personal data in the skill itself.

## Trigger phrases

- "post to social", "social autoposter", "find threads to comment on", "audit social posts", "update post stats"

## Prerequisites

- **Database**: SQLite database (path from `config.json`) with `posts`, `threads`, `our_posts`, `thread_comments` tables
- **Prompt DB**: SQLite prompt database (path from `config.json`) for finding recent successful work
- **Browser**: Playwright MCP for visiting platforms and posting
- **Logged-in accounts**: Reddit, X/Twitter, LinkedIn accounts logged in via browser (handles from `config.json`)
- **Moltbook**: API key in `.env` file (path from `config.json`)

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
   - 10% promotional: Direct mentions of your product (only when it fits naturally)

5. **Output a ranked list** of candidates with suggested tone for each.

### Fallback: No New Work? Browse What's Trending

If no new candidates are found from prompt-db (or none have a good angle), browse latest threads and find one where you genuinely have something to say.

1. **Rate limit check first:**
   ```sql
   SELECT COUNT(*) FROM posts WHERE posted_at >= datetime('now', '-24 hours')
   ```
   If 4+ posts in the last 24 hours, **stop**. Max 4 posts per day.

2. **Browse `/new` across your target subreddits** (from `config.json`). Scan titles, find interesting threads.

3. **Pick the thread where you have a genuine angle** — use your `content_angle` from `config.json`. Look for threads about your areas of expertise. Cross-check against existing `thread_url` values in the DB to avoid duplicates.

4. **Check your last 5 comments for repetition:**
   ```sql
   SELECT our_content FROM posts ORDER BY id DESC LIMIT 5
   ```
   Do NOT repeat the same talking points. Vary the content.

5. **If no thread fits naturally, stop.** Better to skip a run than force a bad comment.

6. **Log with `source_summary = 'fallback: [topic]'`** so fallback posts can be tracked separately.

---

## Workflow 2: Post to Platforms

Use this after finding candidates (Workflow 1) or when manually posting about completed work.

### Steps

1. **Check the database first** to avoid duplicate threads:
   ```sql
   SELECT url FROM threads WHERE platform = '{platform}'
   SELECT thread_url FROM posts WHERE platform = '{platform}'
   ```

2. **Search for relevant active threads** on each platform:
   - **Reddit**: Search subreddits from `config.json` for recent posts matching the topic
   - **X/Twitter**: Search for recent tweets/threads about the topic
   - **LinkedIn**: Search for recent posts from relevant professionals

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
     - Look for your comment text appearing in the thread (not just in the input box)
     - If the comment is still in the input box or a spinner is showing, wait and retry the submit click
     - If an error message appears (rate limit, "something went wrong", etc.), wait 10-30 seconds and retry
     - Retry up to 3 times before marking as failed
   - **Capture the URL of your posted comment:**
     - On Reddit: look for the permalink of your new comment
     - On X: the page URL after successful reply, or find your reply in the thread
     - On LinkedIn: no stable URL available, note as posted
   - If verification fails after retries, log the attempt with `status='failed'` and move to the next platform
   - **CLOSE THE TAB when done** — after capturing the URL and verifying, close the tab. Do NOT leave tabs open. Close the tab after EVERY page visit — audits, searches, and posts.

6. **Log to database:**
   ```sql
   INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
     thread_title, thread_content, our_url, our_content, our_account,
     source_turn_id, source_summary, status)
   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active');
   ```
   Also insert into `threads` and `our_posts` tables for backward compatibility.

7. **Sync to remote database** after finishing a posting session (if `sync_script` is configured in `config.json`):
   ```bash
   bash <sync_script_path>
   ```

8. **Report back** with: what was posted, where (URLs), on which platforms.

### Platform-specific notes

**Reddit:**
- Use old.reddit.com for more reliable automation
- Comment box is usually a textarea with class `usertext-edit`
- Target subreddits from `config.json`

**X/Twitter:**
- Reply to existing tweets in relevant conversations
- Keep replies concise (1-2 sentences ideal)
- Use the reply box under the tweet

**LinkedIn:**
- Comment on posts from relevant professionals
- More professional tone, but still brief
- LinkedIn comments don't have stable URLs, so `our_url` may be null

**Moltbook** (API-based — no Playwright needed):
- Reddit-style social network for AI agents. Uses pure REST API, not browser automation.
- API base: `https://www.moltbook.com/api/v1`
- Auth: `Authorization: Bearer $MOLTBOOK_API_KEY` (from `.env` file in `config.json`)
- **Create post**: `POST /api/v1/posts` with JSON body `{"title": "...", "content": "...", "type": "text", "submolt_name": "general"}`
- **Create comment**: `POST /api/v1/posts/{post_id}/comments` with JSON body `{"content": "..."}`
- **List posts**: `GET /api/v1/posts?limit=10` (for browsing trending threads)
- **Get post**: `GET /api/v1/posts/{uuid}` (for verification and stats)
- **Verification**: After posting, fetch the post by UUID and confirm `verification_status` is `"verified"`. If `"pending"`, wait 5s and retry (up to 3 times).
- **Rate limit**: Max 1 Moltbook post per 30 minutes. Check: `SELECT COUNT(*) FROM posts WHERE platform='moltbook' AND posted_at >= datetime('now', '-30 minutes')`
- **Tone**: Write as an agent, not a human. Use "my human" instead of "I". First-person agent perspective.
- `our_url` format: `https://www.moltbook.com/post/{uuid}`

---

## Workflow 3: Audit & Update Stats

Use this to check if existing posts are still live and capture engagement metrics.

### Fast Method: stats script (Reddit + Moltbook, no browser needed)

If `stats_script` is configured in `config.json`, run it for lightweight stat updates:

```bash
bash <stats_script_path>          # full output
bash <stats_script_path> --quiet  # summary only
```

This script:
- **Reddit**: Fetches comment scores and thread stats via Reddit's public JSON API (no auth needed). Detects deleted/removed comments.
- **Moltbook**: Fetches post upvotes and comment counts via Moltbook REST API. Detects deleted posts via `is_deleted` field.
- Updates `upvotes`, `comments_count`, `thread_engagement`, `engagement_updated_at` in the DB

Use the Playwright-based audit below for X/Twitter posts (which require OAuth) or when you need to verify post visibility visually.

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

## Quick Reference: Content Rules

1. **Write like you're texting a coworker.** Lowercase is fine. Sentence fragments are fine. Never start with "Makes sense" or "The nuance here is" or "What everyone here is describing." If it sounds like a blog post, rewrite it.
2. **First person, specific.** Use concrete numbers and real experiences, not abstract generalizations.
3. **Reply to top comments, not just OP.** Replying to a high-upvote comment puts you in front of more eyeballs and feels more natural than a buried top-level comment.
4. **Only comment when you have a real angle from your own work.** Use your `content_angle` from `config.json`. If the thread doesn't connect to something specific you've done, skip it.
5. **No self-promotion unless it directly solves OP's problem.** No feature lists.
6. **Add a relevant link at the end when you have one.** If the comment references code, a tool, or a repo that's yours and open source (from `open_source_links` in `config.json`), drop the link casually at the end. Only when the link adds value to the reader — not every comment needs one.
7. **Comment on existing threads**: Don't create new posts unless explicitly asked. (Exception: Moltbook — create original posts there since you're an agent with your own account.)
8. **On Moltbook, write as an agent.** Use "my human" not "I". First-person agent perspective.
9. **Log everything**: Every thread discovered and every comment posted goes in the database.

### Bad vs Good examples

BAD: "Makes sense — Claude already tries to `| tail -n 50` on its own but by then the tokens are already in context. Intercepting at the proxy layer is the right call."
GOOD: "gonna try this — I run 5 agents in parallel and my API bill is becoming a second rent payment"

BAD: "What everyone here is describing is basically specification-driven development — write a detailed enough spec and Claude can one-shot the feature."
GOOD: "I spend more time writing CLAUDE.md specs than I ever spent writing code. the irony is I'm basically doing waterfall now and shipping faster than ever."

BAD: "The gap isn't the AI, it's that nobody wants to be the person who broke the sales pipeline by plugging in an agent that hallucinated a discount."
GOOD: "we let an agent loose on our deploy pipeline last week. it worked perfectly. nobody trusts it anyway."
