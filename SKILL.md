---
name: social-autoposter
description: "Automate social media posting across Reddit, X/Twitter, LinkedIn, and Moltbook. Find threads, post comments, create original posts, track engagement stats. Use when: 'post to social', 'social autoposter', 'find threads to comment on', 'create a post', 'audit social posts', 'update post stats'."
user_invocable: true
---

# Social Autoposter

Automates finding, posting, and tracking social media comments and original posts across Reddit, X/Twitter, LinkedIn, and Moltbook.

## Quick Start

| Command | What it does |
|---------|-------------|
| `/social-autoposter` | Comment run — find threads + post comment + log (cron-safe) |
| `/social-autoposter post` | Create an original post/thread (manual only, never cron) |
| `/social-autoposter stats` | Update engagement stats via API |
| `/social-autoposter engage` | Scan and reply to responses on our posts |
| `/social-autoposter audit` | Full browser audit of all posts |

**View your posts live:** `https://s4l.ai/stats/[your_handle]`
The handles come from `config.json → accounts.*.handle/username`. Each platform account has its own URL.

---

## CRITICAL: Platform agents

| Platform | Tool to use | Never use |
|----------|-------------|-----------|
| Reddit | `reddit-agent` MCP | macos-use, Playwright |
| X/Twitter | `twitter-agent` MCP | macos-use, Playwright |
| LinkedIn | macos-use | — |
| Moltbook | curl API | — |

Reddit and Twitter have dedicated MCP agents with pre-loaded browser sessions. Using macos-use or Playwright for these platforms will fail or post from the wrong account.

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
- `projects` — products/repos to mention naturally when relevant (each has `name`, `description`, `website`, `github`, `links`, `topics`). The `links` object has per-platform URLs: `links.reddit`, `links.twitter`, `links.linkedin`, `links.github`, etc. **Always use `links[platform]` for the current platform** — e.g. `links.reddit` when posting on Reddit, `links.twitter` on X. Fall back to `website` or `github` only if `links[platform]` is absent.
- `database` — unused (DB is Neon Postgres via `DATABASE_URL` in `.env`)

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

### 1. Find candidate threads

**Option A — Script (preferred):**
```bash
python3 ~/social-autoposter/scripts/find_threads.py --include-moltbook
```

**Option B — Browse manually:**
Browse `/new` and `/hot` on the subreddits from `config.json`. Also check Moltbook via API.

**Option C — Twitter search (for X/Twitter engagement):**
Use `twitter-agent` MCP to search `https://x.com/search?q=QUERY&f=live`. Build OR queries from topic clusters:
- `"social media leads" OR "reddit marketing" OR "organic distribution"`
- `"startup marketing" OR "indie hacker" OR "first users"`
- `"distribution is everything" OR "reply guy" OR "content marketing"`
- `"AI agent marketing" OR "side project users" OR "B2B lead gen"`

Use `f=live` for recent tweets. Look for tweets with 5+ replies or 50+ views for better reach. Vary search queries across runs to find fresh threads.

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

Follow Content Rules below. 2-3 sentences, first person, specific details from `content_angle`. No product links in top-level comments. When mentioning a project, always use the platform-specific tracking link (e.g. `s4l.ai/x` on X, `s4l.ai/r` on Reddit).

### 6. Post it

**Reddit** (use `reddit-agent` MCP — NOT macos-use or Playwright):
- Use the `reddit-agent` MCP tools to navigate to the thread, submit the comment, and capture the permalink
- Post as the username in `config.json → accounts.reddit.username`
- Verify the comment appeared before capturing the URL

**X/Twitter** (use `twitter-agent` MCP — NOT macos-use or Playwright):
- Use the `twitter-agent` MCP tools to navigate to the tweet, submit the reply, and capture the URL
- Post as the handle in `config.json → accounts.twitter.handle`
- Verify the reply appeared before capturing the URL

**LinkedIn** (browser automation via macos-use):
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

If `sync_script` is set in config.json, run it after logging.

---

## Workflow: Create Post (`/social-autoposter post`)

**Manual only — never run from cron.** Original posts are high-stakes and need human review.

### 1. Cross-posting check

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

**Reddit** (use `reddit-agent` MCP — NOT macos-use or Playwright):
- Use the `reddit-agent` MCP tools to navigate to r/[subreddit]/submit, fill in the title and body, submit, and capture the permalink.

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

## Workflow: Stats (`/social-autoposter stats`)

```bash
python3 ~/social-autoposter/scripts/update_stats.py
```

After running, view updated stats at `https://s4l.ai/stats/[handle]`. The DB syncs to Neon Postgres via `syncfield.sh` (called automatically by `stats.sh`). Changes appear on the website within ~5 minutes.

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

Draft replies: 2-4 sentences, casual, expand the topic. Apply Tiered Reply Strategy.

Post via `reddit-agent` MCP (Reddit), `twitter-agent` MCP (X/Twitter), or API (Moltbook). Update:
```sql
UPDATE replies SET status='replied', our_reply_content=%s, our_reply_url=%s,
  replied_at=NOW() WHERE id=%s
```

### Phase C: X/Twitter replies (use `twitter-agent` MCP)

Use `twitter-agent` MCP tools to navigate to `https://x.com/notifications/mentions`. Find replies to the handle in config.json. Respond to substantive ones (max 5). Log to `replies` table.

**Notification replies default to Tier 2.** When someone replies to your comment, they've already shown interest. This is the ideal moment to naturally mention the product. Don't hard-sell, but do weave in a reference with the tracking link (e.g. `s4l.ai/x`). Examples:
- "yeah thats basically what we built at s4l.ai/x - agent that [does the thing they're describing]"
- "agreed, we use the same approach for [topic] at s4l.ai/x"

Skip Tier 2 only if the reply is off-topic, a one-word response ("Cool"), or crypto/spam.

### Phase D: Edit high-performing posts with project link

Find posts that earned >2 upvotes but haven't had a link appended yet:

```sql
SELECT id, platform, our_url, our_content, thread_title, source_summary
FROM posts
WHERE status='active'
  AND upvotes > 2
  AND posted_at < NOW() - INTERVAL '6 hours'
  AND link_edited_at IS NULL
  AND our_url IS NOT NULL
ORDER BY upvotes DESC
```

For each post:
1. Pick the project from `config.json → projects[]` whose `topics` best match the thread. If no project fits, skip.
2. Write 1 sentence + link (same style as old self-replies):
   - "fwiw I built something for this - [project.website]"
   - "we open sourced the thing I mentioned if anyone wants to poke around [project.github]"
   - "here's the repo if useful [project.github]"
3. Edit the original comment to append a blank line + the link text at the bottom.

**Platform-specific editing:**

**Reddit** (use `reddit-agent` MCP — NOT macos-use or Playwright):
- Use `reddit-agent` MCP tools to navigate to the comment permalink on `old.reddit.com`
- Click "edit" under the comment
- Append `\n\n[link text]` to the existing content
- Save, verify the edit appeared

**Moltbook** (API):
```bash
source ~/social-autoposter/.env
curl -s -X PATCH -H "Authorization: Bearer $MOLTBOOK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"content": "[original content]\n\n[link text]"}' \
  "https://www.moltbook.com/api/v1/posts/[uuid]"
```

**X/Twitter:** Skip — editing is not supported.

**LinkedIn:** Skip — editing is unreliable via automation.

4. After a successful edit, update the DB:
```sql
UPDATE posts SET link_edited_at=NOW(), link_edit_content=%s WHERE id=%s
```

No limit on edits per engage run.

---

## Workflow: Audit (`/social-autoposter audit`)

Visit each post URL via the appropriate agent: `reddit-agent` MCP for Reddit posts, `twitter-agent` MCP for X/Twitter posts, macos-use for LinkedIn. Check status (active/deleted/removed/inactive). Update engagement metrics. Report summary.

---

## Content Rules

### Tone & Voice

1. **Write like you're texting a coworker.** Lowercase fine. Sentence fragments fine. Never start with "Makes sense" or "The nuance here is." If it sounds like a blog post, rewrite it.
2. **First person, specific.** Use concrete details from `content_angle` in config.json. Real numbers, real experiences, not generalizations.
3. **Reply to top comments, not just OP.**
4. **Only comment when there's a genuine angle from the user's work.** Use `content_angle` from config.json. If the thread doesn't connect, skip it.
5. **When mentioning a project, use the platform-specific tracking link** from `config.json → projects[].links`. For example, mention `s4l.ai/x` on X/Twitter, `s4l.ai/r` on Reddit, `s4l.ai/li` on LinkedIn. Never use bare domains like `s4l.ai` — always use the tracking path so PostHog can attribute the traffic. If a project has no `links` entry, use the bare `website` URL.
6. **No product links in top-level comments or original posts.** Earn attention first. When you do mention a project (naturally, in context), use the tracking link above.
7. **On Moltbook, write as an agent.** "my human" not "I".
8. **Log everything.**

### Anti-AI-Detection (applies to ALL content)

8. **No em dashes (—).** Use commas, periods, or regular dashes (-) instead. Em dashes are the #1 "ChatGPT tell."
9. **No markdown formatting in Reddit.** No headers (##), no bold (**text**), no numbered lists. Write in plain paragraphs.
10. **Never cross-post.** One post per topic per community.
11. **No posting limits.** Post as much as needed.
12. **Include imperfections.** Contractions, sentence fragments, casual asides, occasional lowercase.
13. **Vary your openings.** Don't always start with credentials. Sometimes just jump into the topic.
14. **Reply to comments on your posts.** Zero engagement on your own post = bot signal. Reply within 24h.

### Bad vs Good (Comments)

BAD: "Makes sense — Claude already tries to tail the output on its own but by then the tokens are already in context."
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

**Tier 2 — Natural mention:** Conversation touches a topic matching one of the user's projects (from `config.json → projects[].topics`). Mention casually, link only if it adds value. Triggers: "what tool do you use", problem matches a project topic, 2+ replies deep, **or replying to someone who engaged with your post** (notifications/mentions).

**Tier 3 — Direct ask:** They ask for link/try/source. Give it immediately using `projects[].links[current_platform]` from config.json (e.g. `links.reddit` on Reddit, `links.twitter` on X, `links.linkedin` on LinkedIn, `links.github` on GitHub). Fall back to `website` or `github` only if the platform-specific link is missing.

---

## Database Schema

`posts`: id, platform, thread_url, thread_title, our_url, our_content, our_account, posted_at, status, upvotes, comments_count, views, source_summary

Key fields in `posts`: `id, platform, thread_url, thread_title, our_url, our_content, our_account, posted_at, status, upvotes, comments_count, views, source_summary, link_edited_at, link_edit_content`

`replies`: id, post_id, platform, their_author, their_content, our_reply_content, status (pending|replied|skipped|error), depth

---

## Platform Reference

**Reddit:** Use `reddit-agent` MCP (NOT macos-use or Playwright). The `reddit-agent` has a pre-loaded Reddit session. Always use `old.reddit.com` URLs for reliable element targeting.

**X/Twitter:** Use `twitter-agent` MCP (NOT macos-use or Playwright). The `twitter-agent` has a pre-loaded X/Twitter session. Reply to existing tweets, 1-2 sentences ideal.

**LinkedIn:** Professional tone, brief. Comments don't have stable URLs. Browser only.

**Moltbook:** Full REST API, no browser needed. Base: `https://www.moltbook.com/api/v1`. Auth: `Bearer $MOLTBOOK_API_KEY`. Agent-first platform — write as an agent.
