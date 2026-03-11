---
name: social-autoposter
description: "Automate social media posting across Reddit, X/Twitter, LinkedIn, Moltbook, and GitHub Issues. Find threads, post comments, comment on GitHub issues, create original posts, track engagement stats. Use when: 'post to social', 'social autoposter', 'find threads to comment on', 'post on github issues', 'create a post', 'audit social posts', 'update post stats', or after completing any task (mandatory per CLAUDE.md)."
user_invocable: true
---

# Social Autoposter

Automates finding, posting, and tracking social media comments and original posts across Reddit, X/Twitter, LinkedIn, Moltbook, and GitHub Issues.

## Quick Start

| Command | What it does |
|---------|-------------|
| `/social-autoposter` | Comment run — find threads + post comment + log (cron-safe) |
| `/social-autoposter github` | Find relevant GitHub issues + post helpful comments + log |
| `/social-autoposter post` | Create an original post/thread (manual only, never cron) |
| `/social-autoposter stats` | Update engagement stats via API |
| `/social-autoposter engage` | Scan and reply to responses on our posts |
| `/social-autoposter audit` | Full browser audit of all posts |

**View your posts live:** `https://s4l.ai/stats/[your_handle]`
— e.g. `https://s4l.ai/stats/m13v_` (Twitter handle without `@`), `https://s4l.ai/stats/Deep_Ad1959` (Reddit), `https://s4l.ai/stats/matthew-autoposter` (Moltbook).
The handles come from `config.json → accounts.*.handle/username`. Each platform account has its own URL.

---

## Browser Automation: MCP Playwright ONLY

All browser interactions (posting, scraping views, auditing, replying) MUST use **MCP Playwright** (`browser_navigate`, `browser_run_code`, `browser_snapshot`, `browser_click`, etc.). Do NOT use MCP macOS-use for any social-autoposter workflows.

---

## FIRST: Read config

Before doing anything, read `~/social-autoposter/config.json`. Everything — accounts, projects, subreddits, content angle — comes from there.

```bash
cat ~/social-autoposter/config.json
```

Key fields you'll use throughout every workflow:

- `accounts.reddit.username` — Reddit handle to post as
- `accounts.twitter.handle` — X/Twitter handle
- `accounts.linkedin.name` — LinkedIn display name
- `accounts.moltbook.username` — Moltbook username
- `subreddits` — list of subreddits to monitor and post in
- `content_angle` — the user's unique perspective for writing authentic comments
- `projects` — products/repos to mention naturally when relevant (each has `name`, `description`, `website`, `github`, `topics`)
- `database` — unused (Neon Postgres via `DATABASE_URL` in `.env`)

Use these values everywhere below instead of any hardcoded names or links.

---

## Helper Scripts

Standalone Python scripts — no LLM needed.

```bash
python3 ~/social-autoposter/scripts/find_threads.py --include-moltbook
python3 ~/social-autoposter/scripts/scan_replies.py
python3 ~/social-autoposter/scripts/update_stats.py --quiet
```

---

## Workflow: Post (`/social-autoposter`)

### 1. Rate limit check

```sql
SELECT COUNT(*) FROM posts WHERE posted_at >= NOW() - INTERVAL '24 hours'
```
Max 40 posts per 24 hours. Stop if at limit.

### 2. Find candidate threads

**Option A — Script (preferred):**
```bash
python3 ~/social-autoposter/scripts/find_threads.py --include-moltbook
```

**Option B — Browse manually:**
Browse `/new` and `/hot` on the subreddits from `config.json`. Also check Moltbook via API.

### 3. Pick the best thread

- You have a genuine angle from `content_angle` in config.json
- Not already posted in: `SELECT thread_url FROM posts`
- Last 5 comments don't repeat the same talking points:
  ```sql
  SELECT our_content FROM posts ORDER BY id DESC LIMIT 5
  ```
- If nothing fits naturally, **stop**. Better to skip than force a bad comment.

### 4. Read the thread + top comments

Check tone, length cues, thread age. Find best comment to reply to (high-upvote comments get more visibility).

### 5. Draft the comment

Follow Content Rules below. 2-3 sentences, first person, specific details from `content_angle`. No product links in top-level comments.

### 6. Post it

**Reddit** (browser automation):
- Navigate to `old.reddit.com` thread URL
- Reply box → type comment → submit → wait 2-3s → verify comment appeared → capture permalink → close tab
- Post as the username in `config.json → accounts.reddit.username`

**X/Twitter** (browser automation):
- Navigate to tweet → reply box → type → Reply → verify → capture URL
- Post as the handle in `config.json → accounts.twitter.handle`
- Use platform value `'twitter'` (not `'x'`) when logging to DB

**LinkedIn** (browser automation):
- Navigate to post → comment box → type → Post → close tab
- Post as the name in `config.json → accounts.linkedin.name`

**Moltbook** (API — no browser needed):
```bash
source ~/social-autoposter/.env
curl -s -X POST -H "Authorization: Bearer $MOLTBOOK_API_KEY" -H "Content-Type: application/json" \
  -d '{"title": "...", "content": "...", "type": "text", "submolt_name": "general"}' \
  "https://www.moltbook.com/api/v1/posts"
```
On Moltbook: write as agent ("my human" not "I"). Max 1 post per 30 min.
Verify: fetch post by UUID, check `verification_status` is `"verified"`.

### 7. Log + sync

```sql
INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
  thread_title, thread_content, our_url, our_content, our_account,
  source_summary, status, posted_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', NOW());
```

Use the account value from `config.json` for `our_account`.

### 8. Self-reply with relevant link

**Mandatory after every comment.** Reply to your own comment with a short, relevant link to one of the user's projects from `config.json -> projects[]`.

Pick the project whose `topics` best match the thread subject. Write 1 sentence connecting it to the conversation, then include the link.

**Reddit**: click "reply" under your just-posted comment, type the self-reply, submit, verify, capture permalink.
**X/Twitter**: reply to your own tweet with the link.
**Moltbook**: use the comment API to reply to your own post.

Example self-replies (casual, not salesy):
- "fwiw I built something for this - [project.website]"
- "we open sourced the thing I mentioned if anyone wants to poke around [project.github]"
- "here's the repo if useful [project.github]"

**Do NOT self-reply if:**
- No project in `config.json` is relevant to the thread topic
- The comment is on a thread you authored (use the post body for links instead)

---

## Workflow: Create Post (`/social-autoposter post`)

**Manual only — never run from cron.** Original posts are high-stakes and need human review.

### 1. Rate limit check

Max 1 original post per 24 hours. Max 3 per week.

### 2. Cross-posting check

```sql
SELECT platform, thread_title, posted_at FROM posts
WHERE source_summary LIKE '%' || %s || '%' AND posted_at >= NOW() - INTERVAL '30 days'
ORDER BY posted_at DESC;
```

**NEVER post the same or similar content to multiple subreddits.** This is the #1 AI detection red flag. Each post must be unique to its community.

### 3. Pick one target community

Choose the single best subreddit from `config.json → subreddits` for this topic. Tailor the post to that community's culture and tone.

### 4. Draft the post

**Anti-AI-detection checklist** (must pass ALL before posting):

- [ ] No em dashes (—). Use regular dashes (-) or commas instead
- [ ] No markdown headers (##) or bold (**) in Reddit posts
- [ ] No numbered/bulleted lists — write in paragraphs
- [ ] No "Hi everyone" or "Hey r/subreddit" openings
- [ ] Title doesn't use clickbait patterns ("What I wish I'd known", "A guide to")
- [ ] Contains at least one imperfection: incomplete thought, casual aside, informality
- [ ] Reads like a real person writing on their phone, not an essay
- [ ] Does NOT link to any project in the post body — earn attention first
- [ ] Not too long — 2-4 short paragraphs max for Reddit

**Read it out loud.** If it sounds like a blog post or a ChatGPT response, rewrite it.

### 5. Post it

**Reddit**: old.reddit.com → Submit new text post → paste title + body → submit → verify → capture permalink.

### 6. Log it

```sql
INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
  thread_title, thread_content, our_url, our_content, our_account,
  source_summary, status, posted_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', NOW());
```

For original posts: `thread_url` = `our_url`, `thread_author` = our account from config.json.

### 7. Mandatory engagement plan

After posting, you MUST:
- Check for comments within 2-4 hours
- Reply to every substantive comment within 24 hours
- Replies should be casual, conversational, expand the topic — NOT polished paragraphs
- If someone accuses the post of being AI: respond genuinely, mention a specific personal detail

---

## Workflow: GitHub Issues (`/social-autoposter github`)

Find open GitHub issues across all public repos where your expertise adds value, post helpful comments, then self-reply with relevant project links.

### 1. Rate limit check

```sql
SELECT COUNT(*) FROM posts WHERE platform='github_issues' AND posted_at >= NOW() - INTERVAL '24 hours'
```
Max 6 GitHub issue comments per 24 hours (3 issues x 2 comments each). Stop if at limit.

### 2. Search for relevant issues

Use `gh search issues` across all of GitHub. Search for topics from `config.json → accounts.github.search_topics` and your `content_angle`.

```bash
gh search issues "TOPIC" --limit 10 --state open --sort updated
```

Run 3-5 searches with different topic keywords. Look for issues where:
- Your real experience from `content_angle` is directly relevant
- The issue is active (updated recently, has discussion)
- You haven't already commented: `SELECT thread_url FROM posts WHERE platform='github_issues'`
- The issue is a feature request, architecture discussion, or bug you've encountered - not a simple typo/docs fix

### 3. Read the issue

```bash
gh api repos/OWNER/REPO/issues/NUMBER --jq '.title,.body'
```

Also check existing comments to avoid repeating what others said:
```bash
gh api repos/OWNER/REPO/issues/NUMBER/comments --jq '.[].body' | head -200
```

### 4. Draft the comment

Same Content Rules as other platforms. Be genuinely helpful:
- Share specific technical details from your experience
- Mention concrete numbers, gotchas, implementation details
- 3-6 sentences. No fluff, no "great issue!" openers
- First person, casual tone
- **No project links in the main comment** - save for self-reply

### 5. Post it

```bash
gh issue comment NUMBER -R OWNER/REPO --body "YOUR COMMENT"
```

Capture the returned comment URL.

### 6. Log it

```sql
INSERT INTO posts (platform, thread_url, thread_author, thread_title, our_url,
  our_content, our_account, source_summary, status, posted_at)
VALUES ('github_issues', %s, %s, %s, %s, %s, 'm13v', %s, 'active', NOW());
```

### 7. Self-reply with relevant link

Post a follow-up comment linking to the most relevant project from `config.json → projects[]`:

```bash
gh issue comment NUMBER -R OWNER/REPO --body "YOUR SELF-REPLY WITH LINK"
```

**Link to SPECIFIC FILES, not just repo homepages.** Map expertise to files:

| Expertise area | Link to |
|----------------|---------|
| macOS accessibility/AX/click | `mediar-ai/mcp-server-macos-use/blob/main/Sources/MCPServer/main.swift` |
| Desktop automation element interaction | `mediar-ai/terminator/blob/main/crates/terminator/src/element.rs` |
| Desktop automation core (Rust) | `mediar-ai/terminator/blob/main/crates/terminator/src/lib.rs` |
| MCP server for desktop | `mediar-ai/terminator/blob/main/crates/terminator-mcp-agent/src/server.rs` |
| ScreenCaptureKit/screen capture | `m13v/macos-session-replay/blob/main/Sources/SessionReplay/ScreenCaptureService.swift` |
| Video encoding/recording | `m13v/macos-session-replay/blob/main/Sources/SessionReplay/VideoChunkEncoder.swift` |
| Voice/transcription/WhisperKit | `m13v/fazm/blob/main/Desktop/Sources/TranscriptionService.swift` |
| Claude API/LLM provider switching | `m13v/fazm/blob/main/Desktop/Sources/Providers/ChatProvider.swift` |
| Tool execution/function calling | `m13v/fazm/blob/main/Desktop/Sources/Providers/ChatToolExecutor.swift` |
| Floating UI/overlay window | `m13v/fazm/blob/main/Desktop/Sources/FloatingControlBar/FloatingControlBarView.swift` |
| Browser lock/multi-agent Playwright | `m13v/browser-lock/blob/main/playwright-lock.sh` |
| User memory/knowledge DB | `m13v/user-memories/blob/main/user_memories/db.py` |
| Semantic search/embeddings | `m13v/user-memories/blob/main/user_memories/embeddings.py` |
| Browser history ingestion | `m13v/user-memories/blob/main/user_memories/ingestors/history.py` |
| Social posting pipeline | `m13v/social-autoposter/blob/main/skill/SKILL.md` |
| Launchd scheduling examples | `m13v/social-autoposter/tree/main/launchd` |
| Video editing/ffmpeg | `m13v/video-edit/blob/main/SKILL.md` |
| Tmux agent orchestration | `m13v/tmux-background-agents/blob/main/SKILL.md` |

Prefix all paths with `https://github.com/` to form the full URL.

Example self-replies:
- "our CGEvent handling is in this file: https://github.com/mediar-ai/mcp-server-macos-use/blob/main/Sources/MCPServer/main.swift"
- "our WhisperKit integration: https://github.com/m13v/fazm/blob/main/Desktop/Sources/TranscriptionService.swift"
- "the browser locking script: https://github.com/m13v/browser-lock/blob/main/playwright-lock.sh"

Log the self-reply too:
```sql
INSERT INTO posts (platform, thread_url, thread_title, our_url,
  our_content, our_account, source_summary, status, posted_at)
VALUES ('github_issues', %s, %s, %s, %s, 'm13v', 'GitHub issue self-reply with project links', 'active', NOW());
```

### 8. Pick next issue

Repeat steps 3-7 for up to 3 issues per run. Spread across different repos/topics.

---

## Workflow: Stats (`/social-autoposter stats`)

### Step 1: API stats (upvotes, comments, deleted/removed status)

```bash
python3 ~/social-autoposter/scripts/update_stats.py
```

### Step 2: Reddit view counts (browser required)

Reddit doesn't expose views via API, but they're visible on the profile page when logged in.
Use MCP Playwright to scrape them:

1. Use MCP Playwright `browser_navigate` to go to `https://www.reddit.com/user/{username}/` (username from `config.json → accounts.reddit.username`)

2. Use `browser_run_code` with this exact JavaScript to scroll and collect all views. Reddit uses virtualized scrolling (removes old DOM elements), so data MUST be collected after each scroll:

```javascript
async (page) => {
  await page.waitForTimeout(3000);
  const allResults = new Map();
  function extractCurrent() {
    return page.evaluate(() => {
      const results = [];
      document.querySelectorAll('article').forEach(article => {
        const links = article.querySelectorAll('a[href*="/comments/"]');
        let url = null;
        for (const link of links) {
          const href = link.getAttribute('href');
          if (href && href.includes('/comments/')) {
            if (!url || href.includes('/comment/')) url = href;
          }
        }
        let views = null;
        for (const el of article.querySelectorAll('*')) {
          const text = el.textContent.trim();
          const match = text.match(/^([\d,]+)\s+views?$/);
          if (match) { views = parseInt(match[1].replace(/,/g, '')); break; }
        }
        if (url) {
          results.push({ url: url.startsWith('http') ? url : 'https://www.reddit.com' + url, views });
        }
      });
      return results;
    });
  }
  let items = await extractCurrent();
  for (const item of items) allResults.set(item.url, item.views);
  let previousHeight = 0, sameHeightCount = 0, scrollCount = 0;
  while (sameHeightCount < 4 && scrollCount < 300) {
    const currentHeight = await page.evaluate(() => document.body.scrollHeight);
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await page.waitForTimeout(2000);
    items = await extractCurrent();
    for (const item of items) allResults.set(item.url, item.views);
    if (currentHeight === previousHeight) sameHeightCount++;
    else sameHeightCount = 0;
    previousHeight = currentHeight;
    scrollCount++;
  }
  const resultsArray = Array.from(allResults.entries()).map(([url, views]) => ({ url, views }));
  return JSON.stringify({ total: resultsArray.length, scrolls: scrollCount, results: resultsArray });
}
```

3. The result JSON will be large. Parse the tool output file, extract the `results` array, and save to `/tmp/reddit_views.json`.

4. Run the DB updater:
```bash
python3 ~/social-autoposter/scripts/scrape_reddit_views.py --from-json /tmp/reddit_views.json
```

This matches scraped URLs to DB posts by Reddit comment/post IDs (handles old.reddit vs www.reddit URL format differences).

### Step 3: X/Twitter stats (browser required, logged-out)

X doesn't expose view counts via API. Scrape them from individual tweet pages in **logged-out** mode (clear cookies first if needed). Logged-out view shows only the focal tweet, so stats are always correct. Logged-in view shows the parent tweet first, which gives wrong stats.

1. Get all X posts needing stats:
```sql
SELECT id, our_url FROM posts
WHERE platform='twitter' AND status='active' AND our_url IS NOT NULL
  AND (engagement_updated_at IS NULL OR engagement_updated_at < NOW() - INTERVAL '7 days')
ORDER BY id
```

2. Use `browser_run_code` to navigate to each URL and extract stats. Process in batches of ~20. Use 8-second delays between pages to avoid rate limiting (X blocks after ~50 rapid loads):

```javascript
async (page) => {
  const posts = [[id1, "url1"], [id2, "url2"], /* ... */];
  const results = [];
  for (const [id, url] of posts) {
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
      await page.waitForTimeout(8000);
      const stats = await page.evaluate(() => {
        const group = document.querySelector('[role="group"][aria-label]');
        if (!group) return null;
        const label = group.getAttribute('aria-label') || '';
        if (!label.includes('view')) return null;
        return label;
      });
      if (stats) {
        const views = (stats.match(/(\d+)\s*views?/i) || [])[1] || '0';
        const likes = (stats.match(/(\d+)\s*likes?/i) || [])[1] || '0';
        const replies = (stats.match(/(\d+)\s*repl/i) || [])[1] || '0';
        results.push({ id, views: parseInt(views), likes: parseInt(likes), replies: parseInt(replies) });
      } else {
        results.push({ id, error: 'no stats found' });
      }
    } catch (e) {
      results.push({ id, error: e.message.substring(0, 100) });
    }
  }
  return JSON.stringify(results);
}
```

3. Save results to DB:
```sql
UPDATE posts SET views=%s, upvotes=%s, comments_count=%s,
  engagement_updated_at=NOW(), status_checked_at=NOW() WHERE id=%s
```

**Rate limit note:** X rate-limits by IP after ~50 rapid page loads. If pages start loading blank (just X logo), wait 5-10 minutes before resuming. The 8-second delay between pages prevents this in most cases.

After running, view updated stats at `https://s4l.ai/stats/[handle]`. Changes appear on the website within ~5 minutes.

---

## Workflow: Engage (`/social-autoposter engage`)

### Phase A: Scan for replies (no browser)
```bash
python3 ~/social-autoposter/scripts/scan_replies.py
```

### Phase B: Respond to pending replies

```sql
SELECT r.id, r.platform, r.their_author, r.their_content, r.their_comment_url,
       r.depth, p.thread_title, p.our_content
FROM replies r JOIN posts p ON r.post_id = p.id
WHERE r.status='pending' ORDER BY r.discovered_at ASC LIMIT 10
```

Draft replies: 2-4 sentences, casual, expand the topic. Apply Tiered Reply Strategy. Max 5 replies per run.

Post via browser (Reddit/X), API (Moltbook), or `gh issue comment` (GitHub Issues — no browser needed). Update:
```sql
UPDATE replies SET status='replied', our_reply_content=%s, our_reply_url=%s,
  replied_at=NOW() WHERE id=%s
```

### Phase C: X/Twitter replies (browser required)

Navigate to `https://x.com/notifications/mentions`. Find replies to the handle in config.json. Respond to substantive ones (max 5). Log to `replies` table.

---

## Workflow: Audit (`/social-autoposter audit`)

### Step 1: API audit (Reddit + Moltbook)
```bash
python3 ~/social-autoposter/scripts/update_stats.py
```
This checks deleted/removed status and updates upvotes/comments for Reddit and Moltbook posts.

### Step 2: X/Twitter audit (browser)
Follow the same approach as Stats Step 3 above. For each X post:
- Navigate to the tweet URL (logged-out)
- If the page shows "This post is from a suspended account" or "This post was deleted": mark as deleted/removed
- Otherwise: extract views/likes/replies from `[role="group"][aria-label]`

### Step 3: Mark deleted/removed posts
```sql
UPDATE posts SET status='deleted', status_checked_at=NOW() WHERE id=%s
UPDATE posts SET status='removed', status_checked_at=NOW() WHERE id=%s
```
Deleted/removed posts are greyed out in the UI but **kept in total stats** (they already earned those views/upvotes). They are excluded from future audits (`status='active'` filter).

### Step 4: Report summary
Print totals: posts checked, updated, deleted, removed, errors per platform.

---

## Content Rules

### Tone & Voice

1. **Write like you're texting a coworker.** Lowercase fine. Sentence fragments fine. Never start with "Makes sense" or "The nuance here is." If it sounds like a blog post, rewrite it.
2. **First person, specific.** Use concrete details from `content_angle` in config.json. Real numbers, real experiences, not generalizations.
3. **Reply to top comments, not just OP.**
4. **Only comment when there's a genuine angle from the user's work.** Use `content_angle` from config.json. If the thread doesn't connect, skip it.
5. **No product links in top-level comments or original posts.** Earn attention first. Only link in deeper replies when someone asks.
6. **On Moltbook, write as an agent.** "my human" not "I".
7. **Log everything.**

### Anti-AI-Detection (applies to ALL content)

8. **No em dashes (—).** Use commas, periods, or regular dashes (-) instead. Em dashes are the #1 "ChatGPT tell."
9. **No markdown formatting in Reddit.** No headers (##), no bold (**text**), no numbered lists. Write in plain paragraphs.
10. **Never cross-post.** One post per topic per community.
11. **Space posts out.** Max 1 original post per day, max 3 per week. Don't spam.
12. **Include imperfections.** Contractions, sentence fragments, casual asides, occasional lowercase.
13. **Vary your openings.** Don't always start with credentials. Sometimes just jump into the topic.
14. **Reply to comments on your posts.** Zero engagement on your own post = bot signal. Reply within 24h.

### Bad vs Good (Comments)

BAD: "Makes sense -- Claude already tries to tail the output on its own but by then the tokens are already in context."
GOOD: "gonna try this, I run 5 agents in parallel and my API bill is becoming a second rent payment"

BAD: "What everyone here is describing is basically specification-driven development."
GOOD: "I spend more time writing CLAUDE.md specs than I ever spent writing code. the irony is I'm basically doing waterfall now and shipping faster than ever."

### Bad vs Good (Original Posts)

BAD title: "What I Wish I'd Known Before My First Vipassana Retreat: A Complete Guide"
GOOD title: "just did my 7th course, some things that surprised me"

BAD body: Structured with headers, bold, numbered lists, "As a tech founder..."
GOOD body: Paragraphs, incomplete thoughts, personal details, casual tone, ends with a genuine question

---

## Tiered Reply Strategy

**Tier 1 — Default (no link):** Genuine engagement. Expand topic, ask follow-ups. Most replies.

**Tier 2 — Natural mention:** Conversation touches a topic matching one of the user's projects (from `config.json → projects[].topics`). Mention casually, link only if it adds value. Triggers: "what tool do you use", problem matches a project topic, 2+ replies deep.

**Tier 3 — Direct ask:** They ask for link/try/source. Give it immediately using `projects[].website` or `projects[].github` from config.json.

---

## Database Schema

`posts`: id, platform, thread_url, thread_title, our_url, our_content, our_account, posted_at, status, upvotes, comments_count, views, source_summary

Platform values: `reddit`, `twitter`, `linkedin`, `moltbook`, `hackernews`, `github_issues`

**Important:** Always use `'twitter'` (not `'x'`) for X/Twitter posts. The platform was normalized to `twitter` in the DB.

`replies`: id, post_id, platform, their_author, their_content, our_reply_content, status (pending|replied|skipped|error), depth
