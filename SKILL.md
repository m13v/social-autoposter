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

**View your posts live:** `https://s4l.ai/stats/[your_handle]`
— e.g. `https://s4l.ai/stats/m13v_` (Twitter handle without `@`), `https://s4l.ai/stats/Deep_Ad1959` (Reddit), `https://s4l.ai/stats/matthew-autoposter` (Moltbook).
The handles come from `config.json → accounts.*.handle/username`. Each platform account has its own URL.

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

Prefer the helper script (handles verification + self-upvote + outputs correct URL):
```bash
python3 ~/social-autoposter/scripts/moltbook_post.py post --title "..." --content "..." --submolt general
python3 ~/social-autoposter/scripts/moltbook_post.py comment --post-id POST_UUID --content "..."
```

**Moltbook `our_url` rules:**
- Post: `https://www.moltbook.com/post/{post_uuid}`
- Comment: `https://www.moltbook.com/post/{thread_uuid}#{comment_uuid}`
- Use the `url` field from the script's JSON output. Never store bare fragments like `#abc123`.

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

Draft replies: 2-4 sentences, casual, expand the topic. Apply Tiered Reply Strategy. Max 5 replies per run.

Post via browser (Reddit/X) or API (Moltbook). Update:
```sql
UPDATE replies SET status='replied', our_reply_content=%s, our_reply_url=%s,
  replied_at=NOW() WHERE id=%s
```

### Phase C: X/Twitter replies (browser required)

Navigate to `https://x.com/notifications/mentions`. Find replies to the handle in config.json. Respond to substantive ones (max 5). Log to `replies` table.

---

## Workflow: Audit (`/social-autoposter audit`)

Visit each post URL via browser. Check status (active/deleted/removed/inactive). Update engagement metrics. Report summary.

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

**Tier 2 — Natural mention:** Conversation touches a topic matching one of the user's projects (from `config.json → projects[].topics`). Mention casually, link only if it adds value. Triggers: "what tool do you use", problem matches a project topic, 2+ replies deep.

**Tier 3 — Direct ask:** They ask for link/try/source. Give it immediately using `projects[].website` or `projects[].github` from config.json.

---

## Workflow: DM Engage (`/social-autoposter engage` Phase E)

DM conversations are tracked end-to-end across `dms` (conversation header) and `dm_messages` (every message).

### Helper scripts

```bash
# Scan for new DM candidates from replies
python3 ~/social-autoposter/scripts/scan_dm_candidates.py --include-moltbook

# Conversation tracker CLI
python3 ~/social-autoposter/scripts/dm_conversation.py summary     # pipeline overview
python3 ~/social-autoposter/scripts/dm_conversation.py pending     # conversations needing reply
python3 ~/social-autoposter/scripts/dm_conversation.py history --dm-id 5
python3 ~/social-autoposter/scripts/dm_conversation.py find --author tolley
```

### Before sending any DM reply

1. Query conversation history: `dm_conversation.py history --dm-id N`
2. Check tier and decide response strategy (Tier 1/2/3)
3. Draft reply following Content Rules
4. Send via browser (Reddit Chat / LinkedIn / X)
5. Log outbound: `dm_conversation.py log-outbound --dm-id N --content "..."`
6. Update tier if conversation progressed: `dm_conversation.py set-tier --dm-id N --tier 2`

### After receiving DM responses

1. Check Reddit Chat / LinkedIn / X for new inbound messages
2. Log each: `dm_conversation.py log-inbound --dm-id N --author username --content "..."`
3. Set chat URL if missing: `dm_conversation.py set-url --dm-id N --url "https://..."`
4. Conversation auto-marks as `needs_reply`

### Conversation statuses

- `active` - conversation ongoing, no pending action
- `needs_reply` - they replied, we haven't responded yet
- `stale` - no activity for 7+ days
- `converted` - they used/tried our product
- `closed` - conversation ended naturally

---

## Database Schema

`posts`: id, platform, thread_url, thread_title, our_url, our_content, our_account, posted_at, status, upvotes, comments_count, views, source_summary

`replies`: id, post_id, platform, their_author, their_content, our_reply_content, status (pending|replied|skipped|error), depth

`dms`: id, platform, reply_id, post_id, their_author, their_content, our_dm_content, comment_context, status (pending|sent|skipped), skip_reason, chat_url, conversation_status (active|needs_reply|stale|converted|closed), tier (1|2|3), last_message_at, message_count

`dm_messages`: id, dm_id, direction (outbound|inbound), author, content, message_at, logged_at
