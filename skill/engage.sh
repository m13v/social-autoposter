#!/usr/bin/env bash
# engage.sh — Reply engagement loop
# Phase B: Claude drafts and posts replies via Playwright/API (batched, 50 at a time)
# Phase C: Cleanup
# Phase D: Edit high-performing posts (>2 upvotes, 6h+ old) with a project link
# Phase E: DM outreach
# Called by launchd every 2 hours (7200s interval).
# NOTE: Reply scanning (formerly Phase A) now runs on its own hourly launchd job:
# com.m13v.social-scan-replies (skill/run-scan-replies.sh)

set -euo pipefail

# Engage lock: wait up to 60min for previous engage run to finish, then skip
source "$(dirname "$0")/lock.sh"
acquire_lock "engage" 3600

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set in ~/social-autoposter/.env"
    exit 1
fi

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/engage-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

log "=== Engagement Loop Run: $(date) ==="

# ═══════════════════════════════════════════════════════
# (Phase A moved to its own launchd job: com.m13v.social-scan-replies)
# ═══════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════
# PHASE D: Edit high-performing posts with project link
# Runs FIRST — processes ALL eligible posts (no limit)
# ═══════════════════════════════════════════════════════
PHASE_D_START=$(date +%s)
EDITABLE=$(psql "$DATABASE_URL" -t -A -c "
    SELECT json_agg(q) FROM (
        SELECT id, platform, our_url, our_content, thread_title, upvotes, project_name
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
   a. Think about what SEO-optimized guide page would fit this specific thread naturally. Consider the thread's audience, their pain points, industry jargon, and what they'd actually find useful. The page should NOT feel like a landing page; it should feel like a genuine 1000-2000 word guide or resource.
   b. cd into the project repo (landing_pages.repo)
   c. Read src/components/guide-theme.ts to get the project's theme constant (e.g. CYRANO_THEME, PIELINE_THEME). This has the brand name, logo, booking URL, and color config.
   d. Create a NEW standalone page as src/app/t/{seo-friendly-slug}/page.tsx using the **reusable guide components**. Import them from @/components/guide:

      \`\`\`tsx
      import { GuideNavbar, GuideFooter, GuideCTASection, InlineCTA, StickyBottomCTA, ProofBanner, VideoEmbed } from "@/components/guide";
      import { PROJECT_THEME } from "@/components/guide-theme"; // use the actual constant name
      import { CTAButton } from "@/components/cta-button";
      \`\`\`

      **Required page structure (in this order):**
      - \`<Metadata>\` export with title, description, openGraph, twitter tags
      - \`<GuideNavbar theme={THEME} />\` as sticky top nav with CTA button
      - Hero section: badge, h1, subtitle paragraph
      - \`<ProofBanner theme={THEME} quote="..." source="..." metric="..." />\` right after the hero, using a proof_point from config.json (e.g. "20 incidents caught in the first month" or "projecting \$500/day additional revenue per location")
      - \`<VideoEmbed videoUrl="..." title="..." />\` if the project has a demo_video in config.json (embed it after the proof banner)
      - Table of contents nav
      - Sections 1-2 of article content
      - \`<InlineCTA theme={THEME} heading="..." body="..." />\` after section 2 (mid-article CTA, before the reader drops off)
      - Sections 3-5+ of article content (comparison tables, bullet lists with real numbers)
      - \`<GuideCTASection theme={THEME} heading="..." body="..." subtext="..." />\` as the final CTA block
      - \`<StickyBottomCTA theme={THEME} text="..." />\` for a floating bottom bar that appears after scrolling
      - \`<GuideFooter theme={THEME} />\`

      **Content rules:**
      - 1000-2000 words of article content
      - Pull real context from the project's config (pricing, features, proof_points, competitive_positioning) and from web research to make it concrete and authoritative
      - Naturally mention the product as ONE solution among the options discussed; do not make the whole page a sales pitch
      - The InlineCTA heading should relate to the pain point discussed in sections 1-2, not generic "learn more"
      - The ProofBanner should use the strongest, most specific proof point from config.json

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
7. For Reddit: use the Python CDP script (no browser MCP needed):
   python3 $REPO_DIR/scripts/reddit_browser.py edit "COMMENT_PERMALINK_URL" "FULL_NEW_CONTENT"
   Note: pass the FULL content (existing + appended link text), not just the appended part.
   Parse JSON result: {ok:true} = success, {ok:false, error} = handle error.
8. For LinkedIn: use the edit script via linkedin-agent browser (mcp__linkedin-agent__* tools):
   a. Set params: mcp__linkedin-agent__browser_run_code with code:
      async (page) => { await page.evaluate(() => { window.__editParams = { postUrl: "POST_URL", appendText: "\\n\\nLINK_TEXT" }; }); }
   b. Run the script: mcp__linkedin-agent__browser_run_code with filename=$REPO_DIR/scripts/edit_linkedin_comment.js
   c. Parse the JSON result: {ok:true, newText} means success, {ok:false, error} means failure.
   d. If error is 'comment_not_found', mark as skipped. If 'link_already_present', mark as skipped (already edited).
   - For LinkedIn (professional tone): "I've been building something related - URL"
9. After each successful edit, update the DB:
   psql "\$DATABASE_URL" -c "UPDATE posts SET link_edited_at=NOW(), link_edit_content='LINK_TEXT' WHERE id=POST_ID"
10. If a post is SKIPPED (no project match, comment not found, removed by moderation, bad URL), ALWAYS mark it so it won't be retried:
   psql "\$DATABASE_URL" -c "UPDATE posts SET link_edited_at=NOW(), link_edit_content='SKIPPED: REASON' WHERE id=POST_ID"
PROMPT_EOF

    gtimeout 14400 claude --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/linkedin-agent-mcp.json" -p "$(cat "$PHASE_D_PROMPT")" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Phase D claude exited with code $?"
    rm -f "$PHASE_D_PROMPT"
    PHASE_D_ELAPSED=$(( $(date +%s) - PHASE_D_START ))
    PHASE_D_EDITED=$(grep -ci "link_edited_at=NOW()" "$LOG_FILE" 2>/dev/null) || true
    PHASE_D_SKIPPED=$(grep -ci "SKIPPED:" "$LOG_FILE" 2>/dev/null) || true
    python3 "$REPO_DIR/scripts/log_run.py" --script "engage_link_edit" --posted "$PHASE_D_EDITED" --skipped "$PHASE_D_SKIPPED" --failed 0 --cost 0 --elapsed "$PHASE_D_ELAPSED"
else
    log "Phase D: No posts eligible for link edit"
    python3 "$REPO_DIR/scripts/log_run.py" --script "engage_link_edit" --posted 0 --skipped 0 --failed 0 --cost 0 --elapsed 0
fi

# ═══════════════════════════════════════════════════════
# PHASE B: Reddit/Moltbook reply engagement
# Processes one reply at a time to avoid context accumulation
# ═══════════════════════════════════════════════════════

# Reset any 'processing' items older than 2 hours back to 'pending'
# These are items the agent physically posted but crashed before marking 'replied'.
# The in-browser already-replied check (below) prevents re-posting duplicates.
RESET_COUNT=$(psql "$DATABASE_URL" -t -A -c "
    UPDATE replies SET status='pending'
    WHERE status='processing' AND processing_at < NOW() - INTERVAL '2 hours'
    RETURNING id;" | wc -l | tr -d ' ')
[ "$RESET_COUNT" -gt 0 ] && log "Phase B: Reset $RESET_COUNT stuck 'processing' items back to pending"

PENDING_COUNT=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM replies WHERE status='pending' AND platform NOT IN ('linkedin', 'x');")
log "Phase B: $PENDING_COUNT pending Reddit/Moltbook replies"

if [ "$PENDING_COUNT" -gt 0 ]; then
    python3 "$REPO_DIR/scripts/engage_reddit.py" --timeout 5400 2>&1 | tee -a "$LOG_FILE" || log "WARNING: engage_reddit.py exited with code $?"
else
    log "Phase B: No pending replies. Skipping."
fi

# ═══════════════════════════════════════════════════════
# PHASE E: Reddit DM engagement (continue conversations via Chat)
# Finds users who engaged on our posts and DMs them to continue the discussion
# ═══════════════════════════════════════════════════════
PHASE_E_START=$(date +%s)
log "Phase E: Scanning for DM candidates (all platforms)..."
(PYTHONUNBUFFERED=1 python3 "$REPO_DIR/scripts/scan_dm_candidates.py" 2>&1 || true) | tee -a "$LOG_FILE"

DM_PENDING=$(psql "$DATABASE_URL" -t -A -c "SELECT COUNT(*) FROM dms WHERE status='pending';" 2>/dev/null || echo "0")

if [ "$DM_PENDING" -gt 0 ]; then
    log "Phase E: $DM_PENDING DMs to send across all platforms"

    DM_DATA=$(psql "$DATABASE_URL" -t -A -c "
        SELECT json_agg(q) FROM (
            SELECT d.id, d.platform, d.their_author, d.their_content, d.comment_context,
                   d.project_name,
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

### Reddit DMs (use Python CDP script)
python3 $REPO_DIR/scripts/reddit_browser.py compose-dm "THEIR_AUTHOR" "SUBJECT" "DM_TEXT"
Parse JSON result: {ok:true} = success, {ok:false, error} = handle error.
If compose-dm fails (Reddit may redirect to Chat SPA), fall back to mcp__reddit-agent__* browser tools.

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

CRITICAL: Prefer Python CDP scripts where available. Fall back to dedicated browser agents when CDP fails.
- Reddit: python3 scripts/reddit_browser.py (CDP), fall back to mcp__reddit-agent__*
- Twitter: python3 scripts/twitter_browser.py (CDP), fall back to mcp__twitter-agent__*
- LinkedIn: python3 scripts/linkedin_browser.py (CDP), fall back to mcp__linkedin-agent__*
NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools.
If a script or tool call fails, wait 30 seconds and retry (up to 3 times).
PROMPT_EOF

    gtimeout 3600 claude --strict-mcp-config --mcp-config "$HOME/.claude/browser-agent-configs/all-agents-mcp.json" -p "$(cat "$DM_PROMPT")" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: Phase E claude exited with code $?"
    rm -f "$DM_PROMPT"
    PHASE_E_ELAPSED=$(( $(date +%s) - PHASE_E_START ))
    python3 "$REPO_DIR/scripts/log_run.py" --script "engage_dm_outreach" --posted "$DM_PENDING" --skipped 0 --failed 0 --cost 0 --elapsed "$PHASE_E_ELAPSED"
else
    log "Phase E: No pending DMs"
    PHASE_E_ELAPSED=$(( $(date +%s) - PHASE_E_START ))
    python3 "$REPO_DIR/scripts/log_run.py" --script "engage_dm_outreach" --posted 0 --skipped 0 --failed 0 --cost 0 --elapsed "$PHASE_E_ELAPSED"
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
