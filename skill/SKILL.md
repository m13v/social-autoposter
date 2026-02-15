# Social Autoposter Skill

Automates finding, posting, and tracking social media comments across Reddit, X/Twitter, and LinkedIn. Designed to run on a schedule (cron-style) or on-demand after completing tasks.

## Trigger phrases

- "post to social", "social autoposter", "find threads to comment on", "audit social posts", "update post stats"
- Also triggered automatically by CLAUDE.md "After Completing Any Task" workflow

## Prerequisites

- **Database**: `~/.claude/social_posts.db` (SQLite) with `posts`, `threads`, `our_posts`, `thread_comments` tables
- **Prompt DB**: `~/claude-prompt-db/prompts.db` for finding recent successful work
- **Browser**: Playwright MCP for visiting platforms and posting
- **Logged-in accounts**: Reddit (u/Deep_Ad1959), X (@m13v_), LinkedIn (Matthew Diakonov)

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
   - **Reddit**: Search relevant subreddits for recent posts matching the topic
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

7. **Report back** with: what was posted, where (URLs), on which platforms.

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

---

## Workflow 3: Audit & Update Stats

Use this to check if existing posts are still live and capture engagement metrics.

### Fast Method: `stats.sh` (Reddit only, no browser needed)

For Reddit posts, use the lightweight bash script instead of Playwright:

```bash
bash ~/.claude/skills/social-autoposter/stats.sh          # full output
bash ~/.claude/skills/social-autoposter/stats.sh --quiet   # summary only
```

This script:
- Fetches comment scores and thread stats via Reddit's public JSON API (no auth needed)
- Detects deleted/removed comments and updates their status
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

## Quick Reference: Content Rules

1. **Write like you're texting a coworker.** Lowercase is fine. Sentence fragments are fine. Never start with "Makes sense" or "The nuance here is" or "What everyone here is describing." If it sounds like a blog post, rewrite it.
2. **First person, specific.** "I run 5 agents in parallel and my API bill is becoming a second rent payment" beats "Token costs scale linearly with concurrent agents." Always say "I" not "you" or "one."
3. **Reply to top comments, not just OP.** Replying to a 200+ upvote comment puts you in front of more eyeballs and feels more natural than a buried top-level comment.
4. **Only comment when you have a real angle from Matthew's work.** Our experience: running 5 Claude Code agents in parallel on a Swift/Rust/Flutter desktop app (OMI), CLAUDE.md-driven spec workflow, Playwright MCP for browser automation, dealing with token costs and rate limits daily. If the thread doesn't connect to something specific we've done, skip it.
5. **No self-promotion unless it directly solves OP's problem.** No links, no feature lists.
6. **Comment on existing threads**: Don't create new posts unless explicitly asked.
7. **Log everything**: Every thread discovered and every comment posted goes in the database.

### Bad vs Good examples

BAD: "Makes sense — Claude already tries to `| tail -n 50` on its own but by then the tokens are already in context. Intercepting at the proxy layer is the right call."
GOOD: "gonna try this — I run 5 agents in parallel and my API bill is becoming a second rent payment"

BAD: "What everyone here is describing is basically specification-driven development — write a detailed enough spec and Claude can one-shot the feature."
GOOD: "I spend more time writing CLAUDE.md specs than I ever spent writing code. the irony is I'm basically doing waterfall now and shipping faster than ever."

BAD: "The gap isn't the AI, it's that nobody wants to be the person who broke the sales pipeline by plugging in an agent that hallucinated a discount."
GOOD: "we let an agent loose on our deploy pipeline last week. it worked perfectly. nobody trusts it anyway."
