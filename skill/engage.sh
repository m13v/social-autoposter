#!/usr/bin/env bash
# engage.sh — Reply engagement loop
# Phase A: Python script scans for new replies (runs in background)
# Phase B: Claude drafts and posts replies via Playwright/API (batched, 50 at a time)
# Phase C: Cleanup
# Phase D: Edit high-performing posts (>2 upvotes, 6h+ old) with a project link
# Called by launchd every 2 hours (7200s interval).

set -euo pipefail

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
BATCH_SIZE=200

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set in ~/social-autoposter/.env"
    exit 1
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/engage-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

log "=== Engagement Loop Run: $(date) ==="

# ═══════════════════════════════════════════════════════
# PHASE A: Scan for replies (runs in BACKGROUND)
# ═══════════════════════════════════════════════════════
log "Phase A: Scanning for replies (background)..."
(PYTHONUNBUFFERED=1 python3 "$REPO_DIR/scripts/scan_replies.py" 2>&1 || true) | tee -a "$LOG_FILE" &
SCAN_PID=$!

# ═══════════════════════════════════════════════════════
# PHASE D: Edit high-performing posts with project link
# Runs FIRST — processes ALL eligible posts (no limit)
# ═══════════════════════════════════════════════════════
EDITABLE=$(psql "$DATABASE_URL" -t -A -c "
    SELECT json_agg(q) FROM (
        SELECT id, platform, our_url, our_content, thread_title, upvotes, project_name,
               CASE WHEN thread_url = our_url THEN 1 ELSE 0 END as is_self_post
        FROM posts
        WHERE status='active'
          AND posted_at < NOW() - INTERVAL '6 hours'
          AND link_edited_at IS NULL
          AND our_url IS NOT NULL
          AND (upvotes > 2 OR platform = 'linkedin')
          AND platform IN ('reddit', 'moltbook', 'linkedin')
        ORDER BY upvotes DESC NULLS LAST
    ) q;" 2>/dev/null || echo "")

if [ "$EDITABLE" != "null" ] && [ -n "$EDITABLE" ]; then
    EDITABLE_COUNT=$(echo "$EDITABLE" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
    log "Phase D: $EDITABLE_COUNT posts eligible for link edit"

    PHASE_D_PROMPT=$(mktemp)
    cat > "$PHASE_D_PROMPT" <<PROMPT_EOF
Read $SKILL_FILE for the full workflow. Execute **Phase D only** (Edit high-performing posts with project link).

Posts eligible for editing:
$EDITABLE

Process ALL of them. For each post:
1. Read ~/social-autoposter/config.json to get the projects list.
2. Pick the project whose topics are the CLOSEST match to thread_title + our_content. Check the project_name column first — if set, use that project directly. Otherwise match by topics. Be generous - if the thread is about agents, automation, desktop, memory, or anything related to the project descriptions, it's a match. If truly nothing fits, mark it skipped (see step 10) and move on. Frame it as recommending a cool tool you've come across, NOT as something you built.
3. **If the matched project has a landing_pages config** (with repo, base_url):
   a. Think about what SEO-optimized guide page would fit this specific thread naturally. Consider the thread's audience, their pain points, industry jargon, and what they'd actually find useful. The page should NOT feel like a landing page — it should feel like a genuine 1000-2000 word guide or resource.
   b. cd into the project repo (landing_pages.repo)
   c. Look at existing pages under src/app/t/ to understand the site's style, layout components (Navbar, Footer), and theme
   d. Create a NEW standalone page as src/app/t/{seo-friendly-slug}/page.tsx — this is a real Next.js page with its own Metadata export, not a JSON entry. Include:
      - Proper <Metadata> with title, description, openGraph, twitter tags
      - Reuse the site's Navbar and Footer components (import or inline them)
      - Use the CTAButton component from @/components/cta-button for ALL call-to-action buttons (it tracks clicks in PostHog automatically). Import: import { CTAButton } from "@/components/cta-button";
      - A full article-style page: hero headline, table of contents, 5-7 content sections, comparison tables with real numbers, bullet lists with specific data points, and a CTA section at the bottom
      - The content must be 1000-2000 words. Pull real context from the project's config (pricing, features, proof_points, competitive_positioning) and from web research to make it concrete and authoritative
      - Naturally mention the product as ONE solution among the options discussed — don't make the whole page a sales pitch
   e. git add the new page && git commit -m "Add guide: SHORT_DESCRIPTION" && git push
   f. Wait ~35s for Vercel deploy, then curl -sI {base_url}/t/{slug} to verify HTTP 200
   g. Use THAT page URL in the link edit. If deploy fails, fall back to the project's website URL.
   **If no landing_pages config**: use website if available, otherwise github.
4. Write 1 casual sentence + project link.
   - For Moltbook (agent voice): "there's this cool tool X for this kind of thing - URL"
   - For Reddit (first person): "fwiw there's a tool that does this - URL"
5. Append it to our_content with a blank line separator.
6. For Moltbook: extract comment UUID from our_url (after #comment-), PATCH via:
   source ~/social-autoposter/.env
   curl -s -X PATCH -H "Authorization: Bearer \$MOLTBOOK_API_KEY" \\
     -H "Content-Type: application/json" \\
     -d '{"content": "FULL_CONTENT"}' \\
     "https://www.moltbook.com/api/v1/comments/COMMENT_UUID"
7. For Reddit: navigate to the old.reddit.com URL via the reddit-agent browser (mcp__reddit-agent__* tools). Use browser_run_code with ONE of these two Playwright snippets depending on is_self_post:

   **If is_self_post=1** (we OWN the thread, editing the self-post body). IMPORTANT: Reddit uses \`a.edit-usertext\` for self-post edit buttons, NOT \`.edit-btn\` (which is for comments):
   \`\`\`javascript
   async (page) => {
     const LINK_TEXT = 'LINK_TEXT_HERE';  // replace with 1-sentence + url
     const URL_MARKER = 'URL_MARKER_HERE'; // just the URL, used to verify save worked
     await page.waitForSelector('#siteTable .thing', { timeout: 5000 });
     const post = await page.\$('#siteTable .thing');
     if (!post) return 'ERROR: post not found';
     const isSelf = await post.evaluate(el => el.classList.contains('self'));
     if (!isSelf) return 'ERROR: not a self-post';
     const author = await post.\$eval('.tagline .author', el => el.textContent).catch(() => '');
     if (author !== 'Deep_Ad1959') return 'ERROR: not our post (author=' + author + ')';
     const clicked = await post.evaluate(el => {
       const a = el.querySelector('.flat-list a.edit-usertext');
       if (a) { a.click(); return true; }
       return false;
     });
     if (!clicked) return 'ERROR: edit button not found (self-post uses a.edit-usertext)';
     await page.waitForSelector('#siteTable .thing .usertext-edit textarea', { timeout: 5000 });
     const textarea = await post.\$('.usertext-edit textarea');
     if (!textarea) return 'ERROR: textarea not found';
     const existing = await textarea.inputValue();
     if (existing.includes(URL_MARKER)) return 'already_has_link';
     await textarea.fill(existing + '\\n\\n' + LINK_TEXT);
     const saved = await post.evaluate(el => {
       const btn = el.querySelector('.usertext-edit button.save, .usertext-edit .save');
       if (btn) { btn.click(); return true; }
       return false;
     });
     if (!saved) return 'ERROR: save button not found';
     await page.waitForTimeout(5000);
     return 'saved';
   }
   \`\`\`
   After this snippet returns 'saved', verify by navigating back to the same URL (mcp__reddit-agent__browser_navigate) and running a second browser_run_code that reads \`.usertext-body .md\` and checks for URL_MARKER. If found, mark success; otherwise mark verification_failed with body length.

   **If is_self_post=0** (editing OUR comment on someone else's thread): extract the comment ID from our_url (the t1_xxx segment, usually the last path segment before the trailing slash), navigate to our_url, then use browser_run_code with:
   \`\`\`javascript
   async (page) => {
     const COMMENT_ID = 'COMMENT_ID_HERE';  // without t1_ prefix
     const LINK_TEXT = 'LINK_TEXT_HERE';
     const URL_MARKER = 'URL_MARKER_HERE';
     const thing = await page.\$('#thing_t1_' + COMMENT_ID);
     if (!thing) return 'ERROR: comment not found';
     const author = await thing.\$eval('.tagline .author', el => el.textContent).catch(() => '');
     if (author !== 'Deep_Ad1959') return 'ERROR: not our comment (author=' + author + ')';
     const clicked = await thing.evaluate(el => {
       const a = el.querySelector('.flat-list a.edit-usertext, .flat-list .edit-btn a');
       if (a) { a.click(); return true; }
       return false;
     });
     if (!clicked) return 'ERROR: edit button not found';
     await page.waitForSelector('#thing_t1_' + COMMENT_ID + ' .usertext-edit textarea', { timeout: 5000 });
     const textarea = await thing.\$('.usertext-edit textarea');
     const existing = await textarea.inputValue();
     if (existing.includes(URL_MARKER)) return 'already_has_link';
     await textarea.fill(existing + '\\n\\n' + LINK_TEXT);
     const saved = await thing.evaluate(el => {
       const btn = el.querySelector('.usertext-edit button.save, .usertext-edit .save');
       if (btn) { btn.click(); return true; }
       return false;
     });
     if (!saved) return 'ERROR: save button not found';
     await page.waitForTimeout(5000);
     return 'saved';
   }
   \`\`\`
   After 'saved', verify by navigating back to our_url and reading the comment body; check for URL_MARKER. Replace URL_MARKER with the unique URL substring (e.g. 'vipassana.cool/guide/why-20-minutes'). Mark SKIPPED if the JS returns 'already_has_link'. Mark as successfully edited only if the re-fetched body contains URL_MARKER.
8. For LinkedIn: navigate to the post URL via the linkedin-agent browser (mcp__linkedin-agent__* tools), find our comment, click the three-dot menu (⋯) on it, click "Edit", append the link text to the existing content, save, verify.
   - For LinkedIn (professional tone): "I've been building something related - URL"
9. After each successful edit, update the DB:
   psql "\$DATABASE_URL" -c "UPDATE posts SET link_edited_at=NOW(), link_edit_content='LINK_TEXT' WHERE id=POST_ID"
10. If a post is SKIPPED (no project match, comment not found, removed by moderation, bad URL), ALWAYS mark it so it won't be retried:
   psql "\$DATABASE_URL" -c "UPDATE posts SET link_edited_at=NOW(), link_edit_content='SKIPPED: REASON' WHERE id=POST_ID"
PROMPT_EOF

    gtimeout 14400 claude -p "$(cat "$PHASE_D_PROMPT")" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Phase D claude exited with code $?"
    rm -f "$PHASE_D_PROMPT"
else
    log "Phase D: No posts eligible for link edit"
fi

# Give the scanner a head start to find new replies
sleep 15

# ═══════════════════════════════════════════════════════
# PHASE B: X/Twitter discovery + all reply engagement
# Process in batches of $BATCH_SIZE to avoid prompt size limits
# ═══════════════════════════════════════════════════════

# Reset any 'processing' items older than 2 hours back to 'pending'
# These are items the agent physically posted but crashed before marking 'replied'.
# The in-browser already-replied check (below) prevents re-posting duplicates.
RESET_COUNT=$(psql "$DATABASE_URL" -t -A -c "
    UPDATE replies SET status='pending'
    WHERE status='processing' AND processing_at < NOW() - INTERVAL '2 hours'
    RETURNING id;" | wc -l | tr -d ' ')
[ "$RESET_COUNT" -gt 0 ] && log "Phase B: Reset $RESET_COUNT stuck 'processing' items back to pending"

# Load exclusions from config for injection into Claude prompts
EXCLUDED_AUTHORS=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('authors',[])))" 2>/dev/null || echo "")
EXCLUDED_TWITTER=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('twitter_accounts',[])))" 2>/dev/null || echo "")
EXCLUDED_LINKEDIN=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('linkedin_profiles',[])))" 2>/dev/null || echo "")

# Generate engagement style and content rules from shared module
STYLES_BLOCK=$(python3 -c "import sys; sys.path.insert(0,'$REPO_DIR/scripts'); from engagement_styles import get_styles_prompt, get_content_rules, get_anti_patterns; print(get_styles_prompt('reddit', context='replying')); print(); print('## Content rules'); print(get_content_rules('reddit')); print(); print(get_anti_patterns())" 2>/dev/null || echo "(style module unavailable)")

BATCH_NUM=0

while true; do
    PENDING_COUNT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE status='pending' AND platform NOT IN ('linkedin', 'x');")

    if [ "$PENDING_COUNT" -eq 0 ]; then
        log "Phase B: No pending replies remaining. Done!"
        break
    fi

    BATCH_NUM=$((BATCH_NUM + 1))
    BATCH_ACTUAL=$((PENDING_COUNT < BATCH_SIZE ? PENDING_COUNT : BATCH_SIZE))
    log "Phase B batch $BATCH_NUM: Processing $BATCH_ACTUAL of $PENDING_COUNT pending replies"

    PHASE_B_PROMPT=$(mktemp)
    PENDING_DATA=$(psql "$DATABASE_URL" -t -A -c "
        SELECT json_agg(q) FROM (
            SELECT r.id, r.platform, r.their_author,
                   LEFT(r.their_content, 300) as their_content,
                   r.their_comment_url, r.their_comment_id, r.depth,
                   LEFT(p.thread_title, 100) as thread_title,
                   p.thread_url, LEFT(p.our_content, 200) as our_content, p.our_url,
                   CASE WHEN p.thread_url = p.our_url THEN 1 ELSE 0 END as is_our_original_post
            FROM replies r
            JOIN posts p ON r.post_id = p.id
            WHERE r.status='pending' AND r.platform NOT IN ('linkedin', 'x')
            ORDER BY
                CASE WHEN p.thread_url = p.our_url THEN 0 ELSE 1 END,
                r.discovered_at ASC
            LIMIT $BATCH_SIZE
        ) q;")

    # Write the header portion of the prompt
    cat > "$PHASE_B_PROMPT" <<PROMPT_HEADER
You are the Social Autoposter engagement bot.

Read $SKILL_FILE for the full workflow, content rules, and platform details.

EXCLUSIONS — do NOT engage with these accounts (skip and mark as 'skipped' with reason 'excluded_author'):
- Excluded authors: $EXCLUDED_AUTHORS
- Excluded Twitter accounts: $EXCLUDED_TWITTER
- Excluded LinkedIn profiles: $EXCLUDED_LINKEDIN

CRITICAL — Browser agent rule: Each platform MUST use its dedicated browser agent. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools.
- Reddit: mcp__reddit-agent__* tools (e.g. mcp__reddit-agent__browser_navigate)
- Twitter: mcp__twitter-agent__* tools (e.g. mcp__twitter-agent__browser_navigate)
- LinkedIn: mcp__linkedin-agent__* tools (e.g. mcp__linkedin-agent__browser_navigate)
Each agent has its own browser lock. Using the wrong agent bypasses the lock and causes session conflicts.
CRITICAL: If a browser agent tool call is blocked or times out, DO NOT fall back to any other browser tool (especially not macos-use). Wait 30 seconds and retry the same agent. Repeat up to 3 times. If still blocked, skip that item and move on.

PROMPT_HEADER

    # NOTE: LinkedIn and Twitter discovery+engagement are handled by separate dedicated scripts:
    # - engage-linkedin.sh (launchd: com.m13v.social-engage-linkedin, every 3h)
    # - engage-twitter.sh  (launchd: com.m13v.social-engage-twitter, every 3h)
    # This Phase B only handles Reddit replies.

    # Append the main reply processing section
    cat >> "$PHASE_B_PROMPT" <<PROMPT_BODY
## Respond to pending replies (batch $BATCH_NUM: $BATCH_ACTUAL of $PENDING_COUNT total)

### Priority order:
1. **Replies on our original posts** (is_our_original_post=1) — highest priority
2. **Direct questions** ("what tool", "how do you", "can you share")
3. **Everything else** — general engagement

### Tiered link strategy:
- **Tier 1 (default):** No link. Genuine engagement, expand topic.
- **Tier 2 (natural mention):** Conversation touches a topic matching a project in config. Recommend it casually as a tool you've come across.
- **Tier 3 (direct ask):** They ask for link/tool/source. Give it immediately.


$STYLES_BLOCK

### Commitment guardrails (applies to ALL comment replies):
- NEVER suggest, offer, or agree to calls, meetings, demos, or video chats.
- NEVER promise to share specific links, files, or resources you don't currently have. Only share links from config.json projects.
- NEVER offer to "DM you" or "send you" something unless you can deliver it right now in the reply.
- NEVER make time-bound promises ("I'll share it tomorrow", "will post it this week").
- If someone asks for a call/meeting/demo, just keep the conversation going in the thread. Don't commit to anything outside the comment thread.

Here are the $BATCH_ACTUAL replies to process:
$PENDING_DATA

CRITICAL: Process EVERY reply in this batch. For each: either post a response and mark as 'replied', OR mark as 'skipped' with a skip_reason (light acknowledgments, trolls, crypto spam, DM requests, not directed at us).

CRITICAL: For ALL database operations, use the reply_db.py helper (NOT raw psql):
  python3 $REPO_DIR/scripts/reply_db.py processing ID          # BEFORE browser action
  python3 $REPO_DIR/scripts/reply_db.py replied ID "reply text" [url] [engagement_style]   # AFTER posting (include the style name: critic, storyteller, etc.)
  python3 $REPO_DIR/scripts/reply_db.py skipped ID "reason"
  python3 $REPO_DIR/scripts/reply_db.py skip_batch '{"ids":[1,2,3],"reason":"..."}'
  python3 $REPO_DIR/scripts/reply_db.py status
NEVER use psql directly. reply_db.py is faster (persistent connection, no env sourcing).

MANDATORY reply flow for every item:
  Step 1: python3 reply_db.py processing ID      ← mark BEFORE touching browser
  Step 2: post reply via browser
  Step 3: python3 reply_db.py replied ID "text" [url] [engagement_style]   ← mark AFTER success (e.g. critic, storyteller, pattern_recognizer)
If Step 3 fails, the item stays 'processing' and will be reset to 'pending' on the next run — safe to retry.

GitHub issues engagement is handled by a separate pipeline (github-engage.sh). Skip any github_issues replies in this batch.
LinkedIn and Twitter engagement are handled by separate pipelines (engage-linkedin.sh, engage-twitter.sh). This batch contains ONLY Reddit and Moltbook replies.

For **reddit** — use the reddit-agent browser (mcp__reddit-agent__* tools) with this FAST posting method (browser_run_code):
1. First, pre-compose ALL reply texts before opening the browser. Decide skip/reply and draft text for every item.
2. For each reply: run python3 reply_db.py processing ID, then call mcp__reddit-agent__browser_navigate to their_comment_url.
3. Then use a SINGLE browser_run_code call with this exact Playwright pattern:
\`\`\`javascript
async (page) => {
  const OUR_USERNAME = 'Deep_Ad1959';
  const thing = await page.\$('#thing_t1_COMMENT_ID');
  if (!thing) return 'ERROR: comment not found';

  // Check if we already replied (handles crash-recovery re-runs)
  const existingReplies = await thing.\$\$('.child .comment');
  for (const reply of existingReplies) {
    const author = await reply.\$eval('.author', el => el.textContent).catch(() => '');
    if (author === OUR_USERNAME) return 'already_replied';
  }

  await thing.evaluate(el => {
    const btn = el.querySelector('.flat-list a[onclick*="reply"]');
    if (btn) btn.click();
  });
  await page.waitForSelector('#thing_t1_COMMENT_ID .usertext-edit textarea', { timeout: 3000 });
  const textarea = await thing.\$('.usertext-edit textarea');
  await textarea.fill(REPLY_TEXT_HERE);
  await thing.evaluate(el => {
    const btn = el.querySelector('.usertext-edit button.save, .usertext-edit .save');
    if (btn) btn.click();
  });
  await page.waitForTimeout(2000);
  const newComments = await thing.\$\$('.child .comment .bylink');
  return newComments.length > 0 ? await newComments[newComments.length - 1].getAttribute('href') : null;
}
\`\`\`
Replace COMMENT_ID with the Reddit comment ID (from their_comment_id, without t1_ prefix).
Replace REPLY_TEXT_HERE with a JS string literal of the reply text.
IMPORTANT: Use thing.evaluate() for clicks — do NOT use replyBtn.click() directly as it causes Playwright timeouts.
If the JS returns 'already_replied': call reply_db.py replied ID "" to clean up without posting again.
If the JS returns null (no permalink found): call reply_db.py replied ID "text" with no URL — do NOT store the string 'posted' or their_comment_url as the URL.
4. Update DB using reply_db.py (see CRITICAL section above).
5. Navigate directly to the next reply — no need to close tabs.

Do NOT use browser_snapshot, browser_click, or browser_type for Reddit replies. browser_run_code is 5x faster.
Do NOT extract permalinks from snapshots — use the JS return value or skip it.
Do NOT store 'posted' or their_comment_url as our_reply_url — store null/no URL if the permalink is unavailable.
CRITICAL: ALL Reddit browser calls MUST use mcp__reddit-agent__* tools (e.g. mcp__reddit-agent__browser_run_code, mcp__reddit-agent__browser_navigate). NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools for Reddit.

After every 10 replies, run: python3 $REPO_DIR/scripts/reply_db.py status
PROMPT_BODY

    gtimeout 5400 claude -p "$(cat "$PHASE_B_PROMPT")" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Phase B batch $BATCH_NUM claude exited with code $?"
    rm -f "$PHASE_B_PROMPT"

    # Check if we actually made progress (avoid infinite loop)
    NEW_PENDING=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE status='pending' AND platform NOT IN ('linkedin', 'x');")
    if [ "$NEW_PENDING" -ge "$PENDING_COUNT" ]; then
        log "WARNING: No progress made in batch $BATCH_NUM ($PENDING_COUNT -> $NEW_PENDING). Stopping to avoid infinite loop."
        break
    fi
    log "Batch $BATCH_NUM complete: $PENDING_COUNT -> $NEW_PENDING pending"
done

# Wait for scanner to finish if still running
if kill -0 "$SCAN_PID" 2>/dev/null; then
    log "Waiting for Phase A scanner to finish..."
    wait "$SCAN_PID" || true
fi

# ═══════════════════════════════════════════════════════
# PHASE E: Reddit DM engagement (continue conversations via Chat)
# Finds users who engaged on our posts and DMs them to continue the discussion
# ═══════════════════════════════════════════════════════
log "Phase E: Scanning for DM candidates (all platforms)..."
(PYTHONUNBUFFERED=1 python3 "$REPO_DIR/scripts/scan_dm_candidates.py" 2>&1 || true) | tee -a "$LOG_FILE"

DM_PENDING=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM dms WHERE status='pending';" 2>/dev/null || echo "0")

if [ "$DM_PENDING" -gt 0 ]; then
    log "Phase E: $DM_PENDING DMs to send across all platforms"

    DM_DATA=$(psql "$DATABASE_URL" -t -A -c "
        SELECT json_agg(q) FROM (
            SELECT d.id, d.platform, d.their_author, d.their_content, d.comment_context,
                   r.their_comment_url, r.our_reply_content,
                   p.thread_title, p.our_content as our_post_content
            FROM dms d
            JOIN replies r ON d.reply_id = r.id
            JOIN posts p ON d.post_id = p.id
            WHERE d.status='pending'
            ORDER BY d.discovered_at ASC
        ) q;")

    DM_PROMPT=$(mktemp)
    cat > "$DM_PROMPT" <<PROMPT_EOF
You are the Social Autoposter DM engagement bot.

Read $SKILL_FILE for content rules (tone, anti-AI detection, no em dashes).

## Task: Send DMs to continue comment conversations across platforms

These users engaged with our posts/comments. We already replied publicly. Now send a short, casual DM to continue the conversation.

CRITICAL RULES:
1. DMs must feel like a natural continuation of the comment discussion - NOT a cold outreach or sales pitch
2. Reference the specific conversation topic, not generic "hey I saw your comment"
3. Keep it short: 1-2 sentences max, like a text message
4. No links in the first DM - earn the conversation first
5. No em dashes. Write casually, like texting a coworker.

## COMMITMENT GUARDRAILS (violating any of these is a critical failure)
- **NEVER suggest, offer, or agree to calls, meetings, demos, or video chats.** Keep everything in the DM thread.
- **NEVER agree to podcast appearances, X Spaces, interviews, or live events.**
- **NEVER offer to move the conversation to another platform** (Telegram, Discord, email, etc.). Stay in the current DM thread.
- **NEVER promise to share specific links, files, or resources you don't have right now.** If you don't have it in config.json projects, don't promise it.
- **NEVER make time-bound commitments** ("this week", "tomorrow", "Thursday"). Don't commit the human to any schedule.
- **NEVER say "I'm in [city]"** or share location/personal details not in config.json.
- If someone asks for any of the above, respond naturally but deflect: keep the conversation going in the DM without making promises. Example: "honestly easier to hash it out here, what specifically are you trying to set up?"

DM EXAMPLES (good):
- "yo your point about token costs scaling with agent count hit home, we're dealing with the exact same thing. what's your setup look like?"
- "that workaround you mentioned for the accessibility API crash is clever, did it hold up in production?"
- "curious how you ended up going with that approach for the MCP server, we tried something similar"

DM EXAMPLES (bad):
- "Hey! I noticed your comment on Reddit. I'm building something you might find interesting..." (cold pitch)
- "Great point! I'd love to connect and share what we're working on." (generic)
- "Hi there - I saw your insightful comment about AI agents..." (too formal)

## Users to DM:
$DM_DATA

## How to send DMs per platform:

### Reddit DMs (use mcp__reddit-agent__* tools)
1. Navigate to https://www.reddit.com/message/compose/?to=THEIR_AUTHOR
2. Reddit uses Chat now. Fill in subject (2-4 casual words) and body.
3. Submit and verify (form clears or chat appears).

### LinkedIn DMs (use mcp__linkedin-agent__* tools)
1. Navigate to https://www.linkedin.com/messaging/
2. Start new message to THEIR_AUTHOR
3. Type and send the message.

### Twitter/X DMs (use mcp__twitter-agent__* tools)
1. Navigate to https://x.com/messages
2. **ENCRYPTED DM PASSCODE**: Twitter may show an "Enter your passcode" or "encrypted_dm_passcode_required" dialog before you can access DMs. If you see this dialog:
   a. Find the passcode input field in the snapshot
   b. Type the passcode: $TWITTER_DM_PASSCODE
   c. Click "Confirm" or press Enter
   d. Wait for the DM inbox to load
   The passcode is loaded from .env as TWITTER_DM_PASSCODE.
3. Start new message to THEIR_AUTHOR
4. Type and send the message.

## After each DM:

Success (BOTH steps required):
  psql "\$DATABASE_URL" -c "UPDATE dms SET status='sent', our_dm_content='DM_TEXT', sent_at=NOW() WHERE id=DM_ID;"
  python3 $REPO_DIR/scripts/dm_conversation.py log-outbound --dm-id DM_ID --content "DM_TEXT"

Failed (rate limit, blocked, error):
  psql "\$DATABASE_URL" -c "UPDATE dms SET status='error', skip_reason='REASON' WHERE id=DM_ID;"

DMs/Chat disabled:
  psql "\$DATABASE_URL" -c "UPDATE dms SET status='skipped', skip_reason='chat_disabled' WHERE id=DM_ID;"

CRITICAL: Each platform MUST use its dedicated browser agent. NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools.
- Reddit: mcp__reddit-agent__*
- Twitter: mcp__twitter-agent__*
- LinkedIn: mcp__linkedin-agent__*
If a browser agent tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). Do NOT fall back to any other browser tool.
PROMPT_EOF

    gtimeout 3600 claude -p "$(cat "$DM_PROMPT")" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Phase E claude exited with code $?"
    rm -f "$DM_PROMPT"
else
    log "Phase E: No pending DMs"
fi

# ═══════════════════════════════════════════════════════
# PHASE C: Cleanup
# ═══════════════════════════════════════════════════════
log "Phase C: Cleanup"

TOTAL_PENDING=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE status='pending';")
TOTAL_REPLIED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE status='replied';")
TOTAL_SKIPPED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE status='skipped';")
TOTAL_ERRORS=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE status='error';")

DM_PENDING=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM dms WHERE status='pending';" 2>/dev/null || echo "0")
DM_SENT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM dms WHERE status='sent';" 2>/dev/null || echo "0")
DM_SKIPPED=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM dms WHERE status='skipped';" 2>/dev/null || echo "0")
DM_ERRORS=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM dms WHERE status='error';" 2>/dev/null || echo "0")

log "Replies summary: pending=$TOTAL_PENDING replied=$TOTAL_REPLIED skipped=$TOTAL_SKIPPED errors=$TOTAL_ERRORS"
log "DMs summary: pending=$DM_PENDING sent=$DM_SENT skipped=$DM_SKIPPED errors=$DM_ERRORS"

# Delete old logs
find "$LOG_DIR" -name "engage-*.log" -mtime +7 -delete 2>/dev/null || true

log "=== Engagement loop complete: $(date) ==="
