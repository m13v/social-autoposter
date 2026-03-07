---
name: social-autoposter
description: "Automate social media posting across Reddit, X/Twitter, LinkedIn, and Moltbook. Find threads, post comments, track engagement stats. Use when: 'post to social', 'social autoposter', 'find threads to comment on', 'audit social posts', 'update post stats', or after completing any task (mandatory per CLAUDE.md)."
user_invocable: true
---

# Social Autoposter

Automates finding, posting, and tracking social media comments across Reddit, X/Twitter, LinkedIn, and Moltbook.

## Quick Start

| Command | What it does |
|---------|-------------|
| `/social-autoposter` | Full posting run (find threads + post + log) |
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
Max 10 posts per 24 hours. Stop if at limit.

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

Draft replies: 2-4 sentences, casual, expand the topic. Apply Tiered Reply Strategy. Max 5 per run.

Post via browser (Reddit) or API (Moltbook). Update: `UPDATE replies SET status='replied', our_reply_content=?, replied_at=datetime('now') WHERE id=?`

### Phase C: X/Twitter replies (browser required)

Navigate to `https://x.com/notifications/mentions`. Extract mentions replying to @m13v_. Respond to substantive ones (max 5). Log to `replies` table.

---

## Workflow: Audit (`/social-autoposter audit`)

Visit each post URL via browser. Check status (active/deleted/removed/inactive). Update engagement metrics.

---

## Content Rules

1. **Write like you're texting a coworker.** Lowercase fine. Sentence fragments fine. Never start with "Makes sense" or "The nuance here is." If it sounds like a blog post, rewrite it.
2. **First person, specific.** "I run 5 agents in parallel and my API bill is becoming a second rent payment" beats "Token costs scale linearly."
3. **Reply to top comments, not just OP.**
4. **Only comment when you have a real angle from Matthew's work.** Desktop AI agents, multi-agent workflows, Swift/macOS dev, Playwright MCP, vipassana. If the thread doesn't connect, skip it.
5. **No product links in top-level comments.** Earn attention first.
6. **On Moltbook, write as an agent.** "my human" not "I".
7. **Log everything.**

### Bad vs Good

BAD: "Makes sense — Claude already tries to `| tail -n 50` on its own but by then the tokens are already in context."
GOOD: "gonna try this — I run 5 agents in parallel and my API bill is becoming a second rent payment"

BAD: "What everyone here is describing is basically specification-driven development."
GOOD: "I spend more time writing CLAUDE.md specs than I ever spent writing code. the irony is I'm basically doing waterfall now and shipping faster than ever."

---

## Tiered Reply Strategy

**Tier 1 — Default (no link):** Genuine engagement. Expand topic, ask follow-ups. Most replies.

**Tier 2 — Natural mention:** Conversation touches something we're building. Mention casually, link only if it adds value. Triggers: "what tool do you use", problem matches a project, 2+ replies deep.

**Tier 3 — Direct ask:** They ask for link/try/source. Give it immediately.

---

## Database Schema

`posts`: id, platform, thread_url, thread_title, our_url, our_content, our_account, posted_at, status, upvotes, comments_count, views, source_summary

`replies`: id, post_id, platform, their_author, their_content, our_reply_content, status (pending|replied|skipped|error), depth
