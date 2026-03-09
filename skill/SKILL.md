---
name: social-autoposter
description: "Automate social media posting across Reddit, X/Twitter, LinkedIn, and Moltbook. Find threads, post comments, create original posts, track engagement stats. Use when: 'post to social', 'social autoposter', 'find threads to comment on', 'create a post', 'audit social posts', 'update post stats', or after completing any task (mandatory per CLAUDE.md)."
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

## Accounts

- **Reddit**: u/Deep_Ad1959 (logged in via Google with matt@mediar.ai). Use old.reddit.com.
- **X/Twitter**: @m13v_
- **LinkedIn**: Matthew Diakonov
- **Moltbook**: matthew-autoposter (API key in `~/social-autoposter/.env`)

## Our Projects & Links

| Project | What it does | Website | GitHub |
|---------|-------------|---------|--------|
| Fazm | AI computer agent for macOS | https://fazm.ai | — |
| Terminator | Desktop automation framework | https://t8r.tech | https://github.com/mediar-ai/terminator |
| macOS MCP | MCP server for macOS automation | — | https://github.com/mediar-ai/mcp-server-macos-use |
| Vipassana | Resource site for meditators | https://vipassana.cool | https://github.com/m13v/vipassana-cool |
| S4L | Social media autoposter (this tool) | https://s4l.ai | https://github.com/m13v/social-autoposter |

Prefer website links when one exists (drives signups). Use GitHub for open source tools without a website.

## Database

- **Path**: `~/social-autoposter/social_posts.db` (also symlinked at `~/.claude/social_posts.db`)
- **Prompt DB**: `~/claude-prompt-db/prompts.db`

## Helper Scripts

Standalone Python scripts — no LLM needed.

```bash
python3 ~/social-autoposter/scripts/find_threads.py --topic "macOS automation"
python3 ~/social-autoposter/scripts/scan_replies.py
python3 ~/social-autoposter/scripts/update_stats.py --quiet
```

---

## Workflow: Post (`/social-autoposter`)

### 1. Rate limit check

```sql
SELECT COUNT(*) FROM posts WHERE posted_at >= datetime('now', '-24 hours')
```
Max 40 posts per 24 hours. Stop if at limit.

### 2. Find candidate threads

**Option A — Script (preferred):**
```bash
python3 ~/social-autoposter/scripts/find_threads.py --include-moltbook
```

**Option B — Browse manually:**
Browse `/new` and `/hot` on: r/ClaudeAI, r/ClaudeCode, r/AI_Agents, r/ExperiencedDevs, r/macapps, r/vipassana.
Also check Moltbook via API.

### 3. Pick the best thread

- Must have a genuine angle from Matthew's work: building desktop AI agents, running 5 Claude agents in parallel on Swift/Rust/Flutter, CLAUDE.md specs, Playwright MCP, token costs, rate limits, vipassana practice
- Not already posted in: `SELECT thread_url FROM posts`
- Last 5 comments don't repeat: `SELECT our_content FROM posts ORDER BY id DESC LIMIT 5`
- If nothing fits, **stop**

### 4. Read the thread + top comments

Check tone, length cues, thread age. Find best comment to reply to (50+ upvotes = more visibility).

### 5. Draft the comment

Follow Content Rules below. 2-3 sentences, first person, specific. No product links in top-level comments.

### 6. Post it

**Reddit**: old.reddit.com → reply box → type → submit → verify → capture permalink → close tab.
**X/Twitter**: tweet → reply box → type → Reply → verify → capture URL → close tab.
**LinkedIn**: post → comment box → type → Post → close tab.
**Moltbook** (API, no browser):
```bash
source ~/social-autoposter/.env
curl -s -X POST -H "Authorization: Bearer $MOLTBOOK_API_KEY" -H "Content-Type: application/json" \
  -d '{"title": "...", "content": "...", "type": "text", "submolt_name": "general"}' \
  "https://www.moltbook.com/api/v1/posts"
```
On Moltbook: write as agent ("my human" not "I"). Max 1 post per 30 min.

### 7. Log + sync

```sql
INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
  thread_title, thread_content, our_url, our_content, our_account,
  source_summary, status, posted_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', datetime('now'));
```

Then sync: `bash ~/social-autoposter/syncfield.sh`

---

## Workflow: Create Post (`/social-autoposter post`)

**Manual only — never run from cron.** Original posts are high-stakes and need human review.

### 1. Rate limit check

```sql
SELECT COUNT(*) FROM posts WHERE posted_at >= datetime('now', '-24 hours') AND thread_author = 'Deep_Ad1959';
```
Max 1 original post per 24 hours. Max 3 per week.

### 2. Cross-posting check

```sql
SELECT platform, thread_title, posted_at FROM posts
WHERE source_summary LIKE '%' || ? || '%' AND posted_at >= datetime('now', '-30 days')
ORDER BY posted_at DESC;
```
**NEVER post the same or similar content to multiple subreddits.** This is the #1 AI detection red flag. Each post must be unique to its community. If you posted about vipassana in r/vipassana this week, do NOT post about vipassana in r/meditation or r/streamentry.

### 3. Pick one target community

Choose the single best subreddit for this topic. Tailor the post to that community's culture:

| Community | Tone | What works |
|-----------|------|------------|
| r/vipassana | Earnest, practical | Course experiences, daily practice struggles, specific technique questions |
| r/meditation | Casual, broad | General insights, beginner-friendly, "what worked for me" |
| r/streamentry | Technical, experienced | Practice milestones, specific meditation phenomena, dharma discussion |
| r/TheMindIlluminated | Structured, stage-based | TMI stage references, attention/awareness balance |
| r/ClaudeAI, r/ClaudeCode | Dev-casual, memes OK | Tool tips, workflow hacks, cost/rate-limit gripes |

### 4. Draft the post

**Anti-AI-detection checklist** (must pass ALL before posting):

- [ ] No em dashes (—). Use regular dashes (-) or commas instead
- [ ] No markdown headers (##) or bold (**) in Reddit posts — Reddit users don't format like that
- [ ] No numbered/bulleted lists — write in paragraphs like a normal person
- [ ] No "Hi everyone" or "Hey r/subreddit" openings
- [ ] Title doesn't use clickbait patterns ("What I wish I'd known", "What actually changed", "A guide to")
- [ ] Contains at least one imperfection: incomplete thought, casual aside, typo-level informality
- [ ] Reads like a real person writing on their phone, not an essay
- [ ] Does NOT link to vipassana.cool or any project in the post body — earn attention first
- [ ] Not too long — 2-4 short paragraphs max for Reddit

**Read it out loud.** If it sounds like a blog post or a ChatGPT response, rewrite it.

### 5. Post it

**Reddit**: old.reddit.com → Submit new text post → paste title + body → submit → verify → capture permalink.

### 6. Log it

```sql
INSERT INTO posts (platform, thread_url, thread_author, thread_author_handle,
  thread_title, thread_content, our_url, our_content, our_account,
  source_summary, status, posted_at)
VALUES (?, ?, 'Deep_Ad1959', 'u/Deep_Ad1959', ?, ?, ?, ?, 'u/Deep_Ad1959', ?, 'active', datetime('now'));
```

For original posts: `thread_url` = `our_url` (same thing), `thread_author` = our account.

### 7. Mandatory engagement plan

After posting, you MUST:
- Check for comments within 2-4 hours
- Reply to every substantive comment within 24 hours
- Replies should be casual, conversational, expand the topic — NOT polished paragraphs
- If someone accuses the post of being AI: respond genuinely, don't get defensive, mention a specific personal detail

---

## Workflow: Stats (`/social-autoposter stats`)

```bash
python3 ~/social-autoposter/scripts/update_stats.py
```

Or the legacy bash version: `bash ~/social-autoposter/skill/stats.sh`

---

## Workflow: Engage (`/social-autoposter engage`)

### Phase A: Scan for replies (no browser)
```bash
python3 ~/social-autoposter/scripts/scan_replies.py
```

### Phase B: Respond to pending replies

Query pending: `SELECT * FROM replies WHERE status='pending' ORDER BY discovered_at LIMIT 10`

Draft replies: 2-4 sentences, casual, expand the topic. Apply Tiered Reply Strategy. Process all pending replies — skip (with reason) those that don't warrant a response.

Post via browser (Reddit) or API (Moltbook). Update: `UPDATE replies SET status='replied', our_reply_content=?, replied_at=datetime('now') WHERE id=?`

### Phase C: X/Twitter replies (browser required)

Navigate to `https://x.com/notifications/mentions`. Extract mentions replying to @m13v_. Respond to all substantive ones. Log to `replies` table.

---

## Workflow: Audit (`/social-autoposter audit`)

Visit each post URL via browser. Check status (active/deleted/removed/inactive). Update engagement metrics.

---

## Content Rules

### Tone & Voice

1. **Write like you're texting a coworker.** Lowercase fine. Sentence fragments fine. Never start with "Makes sense" or "The nuance here is." If it sounds like a blog post, rewrite it.
2. **First person, specific.** "I run 5 agents in parallel and my API bill is becoming a second rent payment" beats "Token costs scale linearly."
3. **Reply to top comments, not just OP.**
4. **Only comment when you have a real angle from Matthew's work.** Desktop AI agents, multi-agent workflows, Swift/macOS dev, Playwright MCP, vipassana. If the thread doesn't connect, skip it.
5. **No product links in top-level comments or original posts.** Earn attention first. Only link in deeper replies when someone asks.
6. **On Moltbook, write as an agent.** "my human" not "I".
7. **Log everything.**

### Anti-AI-Detection (applies to ALL content)

8. **No em dashes (—).** Use commas, periods, or regular dashes (-) instead. Em dashes are the #1 "ChatGPT tell."
9. **No markdown formatting in Reddit.** No headers (##), no bold (**text**), no numbered lists. Write in plain paragraphs. Reddit users don't format posts like documentation.
10. **Never cross-post.** One post per topic per community. Posting the same content to 4 subs is instant AI detection — people check your post history.
11. **Space posts out.** Max 1 original post per day, max 3 per week. Don't spam.
12. **Include imperfections.** Contractions, sentence fragments, casual asides, occasional lowercase. Real people don't write in perfect paragraphs.
13. **Vary your openings.** Don't always start with credentials ("I've sat X courses", "As a tech founder"). Sometimes just jump into the topic.
14. **Reply to comments on your posts.** Zero engagement on your own post = bot signal. Reply within 24h.

### Bad vs Good (Comments)

BAD: "Makes sense — Claude already tries to `| tail -n 50` on its own but by then the tokens are already in context."
GOOD: "gonna try this, I run 5 agents in parallel and my API bill is becoming a second rent payment"

BAD: "What everyone here is describing is basically specification-driven development."
GOOD: "I spend more time writing CLAUDE.md specs than I ever spent writing code. the irony is I'm basically doing waterfall now and shipping faster than ever."

### Bad vs Good (Original Posts)

BAD title: "What I Wish I'd Known Before My First Vipassana Retreat: A Complete Guide"
GOOD title: "just did my 7th vipassana course, some things that surprised me"

BAD body: "## My Background\n\nAs a tech founder based in SF, I've been practicing Vipassana meditation for several years. Here are my key insights:\n\n1. **The first course is brutal** — ten days of silence...\n2. **Daily practice matters** — I sit twice daily..."
GOOD body: "got back from dhamma mahavana last week. 7th course total. every time I think I know what I'm getting into and every time it's completely different.\n\nthe biggest thing this time was realizing how much my daily practice had been on autopilot. like yeah I sit twice a day but was I actually working? the course showed me I'd been going through the motions for months.\n\nanyone else notice that pattern? where you think practice is solid until a course humbles you?"

---

## Tiered Reply Strategy

**Tier 1 — Default (no link):** Genuine engagement. Expand topic, ask follow-ups. Most replies.

**Tier 2 — Natural mention:** Conversation touches something we're building. Mention casually, link only if it adds value. Triggers: "what tool do you use", problem matches a project, 2+ replies deep.

**Tier 3 — Direct ask:** They ask for link/try/source. Give it immediately.

---

## Database Schema

`posts`: id, platform, thread_url, thread_title, our_url, our_content, our_account, posted_at, status, upvotes, comments_count, views, source_summary

`replies`: id, post_id, platform, their_author, their_content, our_reply_content, status (pending|replied|skipped|error), depth
