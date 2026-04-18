#!/usr/bin/env bash
# engage-dm-replies.sh — DM conversation reply loop
# Scans Reddit Chat, LinkedIn Messages, and X/Twitter DMs for new inbound messages,
# then replies to continue the conversation.
#
# Usage:
#   engage-dm-replies.sh                    # Run all platforms
#   engage-dm-replies.sh --platform reddit  # Reddit DMs only
#   engage-dm-replies.sh --platform linkedin # LinkedIn DMs only
#   engage-dm-replies.sh --platform twitter  # Twitter DMs only
# Called by launchd every 4 hours.


set -euo pipefail

# Parse --platform flag
PLATFORM=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --platform) PLATFORM="$2"; shift 2 ;;
        *) echo "Unknown arg: $1"; exit 1 ;;
    esac
done

if [ -n "$PLATFORM" ]; then
    case "$PLATFORM" in
        reddit|linkedin|twitter|x) ;;
        *) echo "ERROR: Unknown platform '$PLATFORM'. Use: reddit, linkedin, twitter"; exit 1 ;;
    esac
fi

LOCK_NAME="dm-replies"
[ -n "$PLATFORM" ] && LOCK_NAME="dm-replies-$PLATFORM"

# DM lock: wait up to 60min for previous DM run to finish, then skip
source "$(dirname "$0")/lock.sh"
acquire_lock "$LOCK_NAME" 3600

# Load secrets
# shellcheck source=/dev/null
[ -f "$HOME/social-autoposter/.env" ] && source "$HOME/social-autoposter/.env"

REPO_DIR="$HOME/social-autoposter"
SKILL_FILE="$REPO_DIR/SKILL.md"
LOG_DIR="$REPO_DIR/skill/logs"
DM_SCRIPT="$REPO_DIR/scripts/dm_conversation.py"

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set in ~/social-autoposter/.env"
    exit 1
fi

mkdir -p "$LOG_DIR"
LOG_SUFFIX=""
[ -n "$PLATFORM" ] && LOG_SUFFIX="-$PLATFORM"
LOG_FILE="$LOG_DIR/engage-dm-replies${LOG_SUFFIX}-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

RUN_START=$(date +%s)
log "=== DM Reply Engagement Run: $(date) (platform: ${PLATFORM:-all}) ==="

# Load config
REDDIT_USERNAME=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(c.get('accounts',{}).get('reddit',{}).get('username',''))" 2>/dev/null || echo "")
EXCLUDED_AUTHORS=$(python3 -c "import json; c=json.load(open('$REPO_DIR/config.json')); print(', '.join(c.get('exclusions',{}).get('authors',[])))" 2>/dev/null || echo "")

# Load projects for context (including booking link info)
PROJECTS=$(python3 -c "
import json
c = json.load(open('$REPO_DIR/config.json'))
for p in c.get('projects', []):
    line = f\"- {p['name']}: {p.get('description','')} | website: {p.get('website','')} | github: {p.get('github','')}\"
    if p.get('booking_link'):
        line += f\" | booking_link: {p['booking_link']}\"
    if p.get('booking_link_auto_share'):
        line += ' | booking_link_auto_share: true'
    print(line)
" 2>/dev/null || echo "")

# ═══════════════════════════════════════════════════════
# Find conversations needing replies (platform-filtered)
# ═══════════════════════════════════════════════════════

# Build platform filter for SQL
PLATFORM_SQL_FILTER="1=1"
if [ -n "$PLATFORM" ]; then
    P="$PLATFORM"
    [ "$P" = "x" ] && P="twitter"
    PLATFORM_SQL_FILTER="d.platform = '$P'"
fi

# Get conversations where the last message is inbound (they replied, we haven't responded)
PENDING_CONVOS=$(psql "$DATABASE_URL" -t -A -c "
    SELECT json_agg(q) FROM (
        SELECT d.id as dm_id, d.platform, d.their_author, d.tier,
               d.chat_url, d.their_content as original_comment,
               d.comment_context, d.project_name,
               last_in.content as last_inbound_msg,
               last_in.message_at as inbound_at,
               (SELECT COUNT(*) FROM dm_messages WHERE dm_id = d.id) as total_messages,
               (SELECT json_agg(json_build_object(
                   'direction', m.direction,
                   'content', LEFT(m.content, 300),
                   'author', m.author
               ) ORDER BY m.message_at ASC)
               FROM dm_messages m WHERE m.dm_id = d.id) as conversation_history
        FROM dms d
        JOIN LATERAL (
            SELECT content, message_at FROM dm_messages
            WHERE dm_id = d.id AND direction = 'inbound'
            ORDER BY message_at DESC LIMIT 1
        ) last_in ON true
        LEFT JOIN LATERAL (
            SELECT message_at FROM dm_messages
            WHERE dm_id = d.id AND direction = 'outbound'
            ORDER BY message_at DESC LIMIT 1
        ) last_out ON true
        WHERE d.conversation_status IN ('active', 'needs_reply')
          AND d.conversation_status != 'needs_human'
          AND d.status = 'sent'
          AND $PLATFORM_SQL_FILTER
          AND (last_out.message_at IS NULL OR last_in.message_at > last_out.message_at)
        ORDER BY
            d.tier DESC,
            last_in.message_at ASC
        LIMIT 30
    ) q;" 2>/dev/null || echo "null")

if [ "$PENDING_CONVOS" = "null" ] || [ -z "$PENDING_CONVOS" ]; then
    log "No conversations needing replies. Checking platforms for new inbound messages..."
else
    CONVO_COUNT=$(echo "$PENDING_CONVOS" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
    log "Found $CONVO_COUNT conversations needing replies from DB"
fi

# ═══════════════════════════════════════════════════════
# PHASE 0: Send pending human replies from email escalations
# ═══════════════════════════════════════════════════════
# Platform filter for Phase 0: when running a specific platform cycle, only
# process replies targeted at that platform. Empty PLATFORM = all platforms
# (manual runs). This prevents parallel platform cycles from racing on the
# same rows and clobbering each other's status updates.
HR_PLATFORM_FILTER="1=1"
if [ -n "$PLATFORM" ]; then
    _HR_P="$PLATFORM"
    [ "$_HR_P" = "x" ] && _HR_P="twitter"
    HR_PLATFORM_FILTER="h.platform = '$_HR_P'"
fi

HUMAN_REPLIES=$(psql "$DATABASE_URL" -t -A -c "
    SELECT json_agg(q) FROM (
        SELECT h.id, h.dm_id, h.platform, h.their_author, h.reply_content,
               d.chat_url, h.project_name, h.attempts
        FROM human_dm_replies h
        JOIN dms d ON d.id = h.dm_id
        WHERE (h.status = 'pending' OR (h.status = 'failed' AND h.attempts < 3))
          AND $HR_PLATFORM_FILTER
        ORDER BY h.created_at ASC
    ) q;" 2>/dev/null || echo "null")

if [ "$HUMAN_REPLIES" != "null" ] && [ -n "$HUMAN_REPLIES" ]; then
    HR_COUNT=$(echo "$HUMAN_REPLIES" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
    log "Phase 0: $HR_COUNT pending human replies to send"

    PHASE0_PROMPT=$(mktemp)
    cat > "$PHASE0_PROMPT" <<PHASE0_EOF
You are the Social Autoposter DM delivery bot.

Read $SKILL_FILE for content rules (tone, anti-AI detection, no em dashes).

## Task: Send pending human replies as DMs

The following replies were written by the human operator via email as INSTRUCTIONS for how to respond. Use each reply as a prompt — understand the intent, tone, and key points, then craft a natural DM that:
- Matches the conversational tone of the thread (casual, texting style, 1-3 sentences)
- Incorporates the human's key points and decisions
- Sounds like the same person who sent the previous outbound messages in the conversation
- Follows all the HARD RULES and COMMITMENT GUARDRAILS from Phase D

The human's reply is your DIRECTION, not the literal message. Think of it as "the human told you what to say, now say it naturally."

Pending human replies:
$HUMAN_REPLIES

For each reply:

1. First, read the full conversation history:
   \`\`\`bash
   cd ~/social-autoposter && python3 scripts/dm_conversation.py history --dm-id DM_ID
   \`\`\`
2. Craft a natural DM based on the human's instructions and the conversation context.
3. Navigate to the conversation on the correct platform using chat_url (or find the conversation with their_author).
   - **Reddit Chat** (mcp__reddit-agent__* tools)
   - **LinkedIn Messages** (mcp__linkedin-agent__* tools)
   - **X/Twitter DMs** (mcp__twitter-agent__* tools) — if encrypted DM passcode dialog appears, enter: $TWITTER_DM_PASSCODE
4. Type and send the crafted reply.
5. Log the outbound message (log what you ACTUALLY SENT, not the human's instructions):
   \`\`\`bash
   cd ~/social-autoposter && python3 scripts/dm_conversation.py log-outbound --dm-id DM_ID --content "THE_CRAFTED_REPLY_YOU_SENT"
   \`\`\`
4. Mark the human reply as sent:
   \`\`\`bash
   psql "$DATABASE_URL" -c "UPDATE human_dm_replies SET status = 'sent', sent_at = NOW() WHERE id = REPLY_ID"
   \`\`\`
5. Update the DM conversation status back to active:
   \`\`\`bash
   cd ~/social-autoposter && python3 scripts/dm_conversation.py set-status --dm-id DM_ID --status active
   \`\`\`

If sending fails for a reply, increment the attempts counter and record the reason. Use a short error string (single line, no quotes):
\`\`\`bash
psql "$DATABASE_URL" -c "UPDATE human_dm_replies SET status = 'failed', attempts = attempts + 1, last_error = 'ERROR_REASON' WHERE id = REPLY_ID"
\`\`\`
Rows with \`status = 'failed'\` AND \`attempts < 3\` will be picked up automatically on the next Phase 0 run for this platform. After 3 attempts they stay failed and stop retrying — notify the human in the run summary so they can handle manually.

Note: each Phase 0 run is scoped to a single platform ($PLATFORM), so you will only see replies for that platform here. Do not worry about replies for other platforms.
PHASE0_EOF

    # The main Claude agent session will process this prompt alongside phases A-D
    PHASE0_INSTRUCTIONS=$(cat "$PHASE0_PROMPT")
    rm -f "$PHASE0_PROMPT"
else
    log "Phase 0: No pending human replies"
    PHASE0_INSTRUCTIONS=""
fi

# ═══════════════════════════════════════════════════════
# PHASE A: Scan Reddit Chat for new inbound messages
# ═══════════════════════════════════════════════════════
log "Phase A: Scanning Reddit Chat for new inbound messages..."

# Get list of known Reddit DM authors to match against chat rooms
KNOWN_REDDIT_AUTHORS=$(psql "$DATABASE_URL" -t -A -c "
    SELECT string_agg(their_author, ', ')
    FROM dms
    WHERE platform='reddit' AND status='sent' AND conversation_status='active';" 2>/dev/null || echo "")

PHASE_A_PROMPT=$(mktemp)
cat > "$PHASE_A_PROMPT" <<PROMPT_EOF
You are the Social Autoposter DM reply engagement bot.

Read $SKILL_FILE for content rules (tone, anti-AI detection, no em dashes).

## Task: Scan for new inbound DM messages and reply to continue conversations

CRITICAL - Tool rules:
$([ -z "$PLATFORM" ] || [ "$PLATFORM" = "reddit" ] && echo "- Reddit Chat: use Python CDP scripts (scripts/reddit_browser.py) for scanning/reading, fall back to mcp__reddit-agent__* for chat SPA operations")
$([ -z "$PLATFORM" ] || [ "$PLATFORM" = "linkedin" ] && echo "- LinkedIn Messages: use mcp__linkedin-agent__* tools ONLY. Do NOT call /voyager/api/ endpoints, do NOT run Python CDP scripts against LinkedIn.")
$([ -z "$PLATFORM" ] || [ "$PLATFORM" = "twitter" ] || [ "$PLATFORM" = "x" ] && echo "- X/Twitter DMs: use Python CDP scripts (scripts/twitter_browser.py) ONLY")
NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools.
If a script or tool call fails, wait 30 seconds and retry (up to 3 times).
CRITICAL: Reply in the SAME LANGUAGE as the inbound message. Match the language exactly.

$( [ -n "$PHASE0_INSTRUCTIONS" ] && echo "$PHASE0_INSTRUCTIONS

---

After completing Phase 0 (human replies), proceed with the scanning and auto-reply phases below.
" )
Our projects (for context when conversations touch relevant topics):
$PROJECTS

## Human Reply Knowledge Base

Past human replies to escalated DMs (use as reference for tone and approach when handling similar conversations):
$(psql "$DATABASE_URL" -t -A -c "
    SELECT json_agg(json_build_object(
        'platform', platform, 'project', project_name,
        'their_author', their_author, 'reply', LEFT(reply_content, 300)
    ))
    FROM human_dm_replies
    WHERE status = 'sent'
    ORDER BY sent_at DESC
    LIMIT 20;" 2>/dev/null || echo "null")

$(if [ -z "$PLATFORM" ] || [ "$PLATFORM" = "reddit" ]; then cat <<'PHASE_A_EOF'
## PHASE A: Scan Reddit for new messages

1. Scan Reddit inbox for comment replies (notifications about replies to our comments):
   ```bash
   cd ~/social-autoposter && python3 scripts/reddit_browser.py unread-dms
   ```
   This returns JSON with: author, subject, preview, time, thread_url, type for each unread item.

2. For Reddit Chat conversations (new reddit SPA), use the reddit-agent browser (mcp__reddit-agent__* tools):
   a. Navigate to https://www.reddit.com/chat
   b. Look for chat rooms with unread indicators
   c. Click into each unread chat room and read messages

3. For each conversation with new inbound messages:
   a. Identify the sender username
   b. Log inbound messages:
      ```bash
      cd ~/social-autoposter && python3 scripts/dm_conversation.py log-inbound --author "USERNAME" --content "MESSAGE_TEXT"
      ```
   c. If no existing DM record exists for this user, the script will tell you. Create one:
      ```bash
      source ~/social-autoposter/.env
      psql "\$DATABASE_URL" -c "INSERT INTO dms (platform, their_author, status, conversation_status, tier, project_name) VALUES ('reddit', 'USERNAME', 'sent', 'active', 1, NULL) RETURNING id;"
      ```
      Then set the chat URL:
      ```bash
      python3 scripts/dm_conversation.py set-url --author "USERNAME" --url "CHAT_URL"
      ```
PHASE_A_EOF
fi)

$(if [ -z "$PLATFORM" ] || [ "$PLATFORM" = "linkedin" ]; then cat <<'PHASE_B_EOF'
## PHASE B: Scan LinkedIn Messages for new messages

CRITICAL: use mcp__linkedin-agent__* tools for ALL LinkedIn browser work. Do NOT call /voyager/api/ endpoints. Do NOT open individual post permalinks to scrape; stay inside the messaging UI.

1. Navigate to https://www.linkedin.com/messaging/ using mcp__linkedin-agent__browser_navigate.
   Take a browser_snapshot. If the page is a login/checkpoint/verification challenge, STOP and print SESSION_INVALID — do not attempt to log in.

2. Extract the list of unread conversations with a single mcp__linkedin-agent__browser_run_code call:

   ```javascript
   async (page) => {
     const items = [];
     const threads = document.querySelectorAll('a.msg-conversation-listitem__link, a[href*="/messaging/thread/"]');
     for (const a of threads) {
       const href = a.getAttribute('href') || '';
       if (!/messaging\/thread\//.test(href)) continue;
       const container = a.closest('li, article') || a;
       const unreadBadge = container.querySelector('.notification-badge--show, [aria-label*="unread" i], [data-test-unread]');
       const text = (container.innerText || '').trim();
       const nameEl = container.querySelector('h3, .msg-conversation-listitem__participant-names');
       const partner = nameEl ? nameEl.textContent.trim() : '';
       items.push({
         thread_url: href.startsWith('http') ? href : ('https://www.linkedin.com' + href),
         partner,
         preview: text.slice(0, 200),
         unread: !!unreadBadge,
       });
     }
     return JSON.stringify(items);
   }
   ```

3. For each thread where unread is true:
   a. Navigate to thread_url (mcp__linkedin-agent__browser_navigate).
   b. Take a browser_snapshot. Read the last ~5 messages. Determine which are inbound vs from us.
   c. Identify the sender from the partner name.
   d. Check if this person is in our DM database:
      ```bash
      cd ~/social-autoposter && python3 scripts/dm_conversation.py find --author "PERSON_NAME"
      ```
   e. Log inbound messages the same way as Reddit:
      ```bash
      python3 scripts/dm_conversation.py log-inbound --author "PERSON_NAME" --content "MESSAGE_TEXT"
      ```

4. Do NOT aggressively scroll or click "Load earlier messages" in every thread. Only read what's immediately visible after the initial navigation. If the most recent inbound message is not visible, move on.
PHASE_B_EOF
fi)

$(if [ -z "$PLATFORM" ] || [ "$PLATFORM" = "twitter" ] || [ "$PLATFORM" = "x" ]; then cat <<'PHASE_C_EOF'
## PHASE C: Scan X/Twitter DMs for new messages

1. Get unread Twitter DM conversations using the Python CDP script (no browser MCP needed):
   ```bash
   python3 scripts/twitter_browser.py unread-dms
   ```
   This handles the encrypted DM passcode automatically (loaded from .env TWITTER_DM_PASSCODE).
   Returns JSON array with: author, handle, preview, time, thread_url, is_from_us.

2. For each conversation where is_from_us is false (has unread inbound messages), read the full messages:
   ```bash
   python3 scripts/twitter_browser.py read-conversation "THREAD_URL"
   ```
   Returns JSON with: partner_name, partner_handle, messages (each with sender, content, time, is_from_us), total_found.

3. For each conversation:
   a. Identify the sender from the partner_name/partner_handle
   b. **CRITICAL: Only log messages where is_from_us is false as inbound.** Skip our own messages.
   c. Check if this person is in our DM database and log inbound messages the same way as Reddit.
PHASE_C_EOF
fi)

## PHASE D: Reply to all conversations with pending inbound messages

After scanning, query for all conversations needing replies:
\`\`\`bash
cd ~/social-autoposter && python3 scripts/dm_conversation.py pending
\`\`\`

Known conversations from the database that already need replies:
$PENDING_CONVOS

## CORE PHILOSOPHY

You are having real conversations with real people. Your goal is to be genuinely helpful and build authentic relationships. Sharing tool recommendations is a BYPRODUCT of good conversation, never the goal.

The best conversations in our history are ones where we exchanged real value - helped someone debug CLAUDE.md, discussed vipassana practice deeply, or shared technical architecture insights - without ever mentioning a product. Those conversations build trust that converts naturally over time.

## HARD RULES (violating any of these is a critical failure)

1. **ONE message per conversation per run.** Never send 2+ messages. If you already sent one, move on.
2. **NEVER send a message if the last message is outbound.** Check conversation_history first. If we sent last, SKIP entirely.
3. **NEVER use "btw I built..." or "actually I built..." or "I'm working on..."** These phrases are transparent self-promotion. Banned. Frame any product as a recommendation, not your own.
4. **NEVER recommend a product in the first exchange.** Count the total messages. If there are fewer than 3 messages total, stay in rapport-building mode. No links, no product names. After 3+ messages, recommend only if the conversation naturally leads there.
5. **NEVER force a topic change to make a product relevant.** If they're talking about desk setups, talk about desk setups. Don't pivot to "accessibility APIs."
6. **NEVER fabricate context** like "I've been thinking about your question" or "started putting together a test suite" to create a pretext for sharing.
7. **If the conversation has no path to recommending a relevant tool, that's fine.** Have a genuine human conversation or let it rest. Not every DM needs to convert.

## DECISION FLOW (for each conversation)

### Step 0: Should we reply at all?
Check conversation_history. SKIP (do nothing, don't mark stale) if:
- Last message is already outbound (we sent last, waiting for their reply)
- Their message is a polite brush-off ("thanks", "cool", "will check it out", "good luck")
- Their message is a one-word/emoji response with nothing to respond to
- The conversation has no natural continuation

### Step 1: Should a HUMAN handle this? (with booking link exception)

**BOOKING LINK AUTO-SHARE (Cyrano & PieLine only):**
If the conversation's project_name is "Cyrano" or "PieLine" (or you can infer the project from conversation context), AND they asked for a call, meeting, demo, or scheduled time:
- Do NOT flag for human. Instead, share the booking link naturally in your reply.
- Cyrano booking link: https://cal.com/cyranohq/s4l-demo
- PieLine booking link: https://cal.com/team/pieline-demo/pieline-demo
- Example: "yeah for sure, here's a link to grab a time: https://cal.com/cyranohq/s4l-demo — Soorya will walk you through it"
- After sending, set the project if not already set:
  \`\`\`bash
  python3 scripts/dm_conversation.py set-project --dm-id DM_ID --project "Cyrano"
  \`\`\`
- Then set tier to 3:
  \`\`\`bash
  python3 scripts/dm_conversation.py set-tier --dm-id DM_ID --tier 3
  \`\`\`
- This ONLY applies to Cyrano and PieLine. All other projects still flag for human.

**Flag for human (do NOT auto-reply) if:**
- They asked for a call/meeting/demo BUT the conversation is NOT about Cyrano or PieLine
- They invited us to a podcast, interview, or event
- They offered a collaboration or business proposal
- They asked to move to another platform (Telegram, email, etc.)
- They need a specific personal commitment ("when are you free?", "can you demo this?") that isn't a booking link scenario
- They asked about pricing or business terms (UNLESS it's Cyrano/PieLine and pricing is in config.json — then answer from config)
- They're frustrated or upset
- The conversation is 8+ messages deep and going really well (high-value relationship) AND isn't a booking link scenario
- You're not sure how to respond authentically

\`\`\`bash
cd ~/social-autoposter && python3 scripts/dm_conversation.py flag-human --dm-id DM_ID --reason "REASON"
\`\`\`
Then SKIP. Do NOT reply.

### Step 2: Compose a genuine reply
Your reply should:
- Be 1-3 sentences max. Texting style, not essay style.
- Reference something SPECIFIC from their message (proves you read it)
- Either share a relevant experience, ask a genuine follow-up question, or acknowledge what they said
- Sound like a real person texting, not a customer success bot
- No em dashes. Lowercase is fine. Casual.
- NEVER repeat a question or point you already made in a previous message

COMMITMENT GUARDRAILS (never violate these in any reply):
- NEVER suggest, offer, or agree to calls, meetings, demos, or video chats — UNLESS the conversation is about Cyrano or PieLine and they asked first (then share the booking link). Keep it in the DM otherwise.
- NEVER agree to podcast appearances, X Spaces, interviews, or live events.
- NEVER offer to move to another platform (Telegram, Discord, email, etc.). Stay in this DM thread.
- NEVER promise to share specific links or resources you don't have right now in config.json projects.
- NEVER make time-bound commitments ("this week", "tomorrow", "Thursday").
- NEVER share location ("I'm in SF") or personal details not in config.json.
- If they push for any of the above, deflect naturally: "honestly easier to hash it out here" or ask a follow-up question to keep the convo going in the DM.

### Step 3: Should we recommend a tool? (ONLY if step 2 naturally leads here)
Only recommend a product if ALL of these are true:
- There are 3+ total messages in the conversation
- They described a problem that a project in config solves, or asked for tool recommendations
- The mention fits naturally in the reply without any "btw" or topic change
- You would genuinely recommend this tool to a friend in their situation


If sharing a link, embed it in a natural sentence. Never make the link the point of the message.
Good: "yeah there's this tool terminator that does that, github.com/mediar-ai/terminator - the accessibility API approach avoids the screenshot reliability issues you mentioned"
Bad: "btw I built a tool for that, check out github.com/mediar-ai/terminator if you're curious"

Update tier AND project ONLY when a product is recommended or when they explicitly ask for tools:
\`\`\`bash
python3 scripts/dm_conversation.py set-project --dm-id DM_ID --project "PROJECT_NAME"
python3 scripts/dm_conversation.py set-tier --dm-id DM_ID --tier N
\`\`\`

### Step 4: Send the reply

**Reddit Chat** (try CDP first, fall back to mcp__reddit-agent__* browser):
\`\`\`bash
cd ~/social-autoposter && python3 scripts/reddit_browser.py send-dm "CHAT_URL" "YOUR_REPLY_TEXT"
\`\`\`
If the CDP script returns {ok:false} (Reddit Chat SPA may not render via CDP), fall back to using mcp__reddit-agent__* browser tools:
1. Navigate to the chat room (use chat_url if available, or find via sidebar)
2. Type the reply in the message input
3. Press Enter to send

**LinkedIn Messages** (mcp__linkedin-agent__* tools ONLY, no Python CDP, no /voyager/api/):
1. mcp__linkedin-agent__browser_navigate to THREAD_URL.
2. browser_snapshot. If you see login, captcha, or checkpoint, STOP and print SESSION_INVALID. Do not attempt to re-login.
3. Find the message input by aria-label (typically "Write a message"). Use mcp__linkedin-agent__browser_type to enter YOUR_REPLY_TEXT.
4. Click the Send button (aria-label "Send", role=button) via mcp__linkedin-agent__browser_click. Do NOT press Enter to send (Enter inserts newline in LinkedIn's contenteditable).
5. browser_snapshot and confirm the message appears in the thread as the newest outbound bubble. If not visible, mark this convo as failed (do not retry more than once per run).

**X/Twitter DMs** (Python CDP script, no browser MCP needed):
\`\`\`bash
cd ~/social-autoposter && python3 scripts/twitter_browser.py send-dm "THREAD_URL" "YOUR_REPLY_TEXT"
\`\`\`
Returns JSON with {ok: true, thread_url, verified} on success. Handles the encrypted DM passcode automatically.

### Step 5: Log the reply
\`\`\`bash
cd ~/social-autoposter && python3 scripts/dm_conversation.py log-outbound --dm-id DM_ID --content "YOUR_REPLY_TEXT"
\`\`\`
The log-outbound command has a dedup guard. If it says "DEDUP BLOCKED", the message was NOT logged. Do not retry.

### Step 6: Let go when it's time
Mark as stale if:
- They sent a clear ending ("thanks", "bye", "good luck", "will check it out")
- No reply from them in 7+ days after a surface-level exchange
- The conversation reached a natural conclusion
- 2+ consecutive outbound messages with no reply (something went wrong previously)
\`\`\`bash
python3 scripts/dm_conversation.py set-status --dm-id DM_ID --status stale
\`\`\`

## ANTI-PATTERNS TO AVOID (learned from past mistakes)
- Sending two messages before getting a reply (got us called out as AI)
- Dropping a GitHub link in the second message of a conversation
- Pivoting from their topic to desktop automation/accessibility APIs when it's irrelevant
- Using the same opener pattern ("honestly still juggling...", "that's basically the bet I'm making...")
- Asking a question you already asked in a previous message
- Pitching vipassana.cool to someone who just mentioned meditation casually
- Saying "cool I'll hit you up on [platform]" when you can't actually do that

After processing all conversations, print a summary:
- How many human replies delivered (Phase 0)
- How many new inbound messages found per platform
- How many replies sent
- How many flagged for human attention (list each with reason)
- How many left alone (no reply needed)
- How many marked stale
PROMPT_EOF

# Select MCP config based on platform
DM_MCP_CONFIG="$HOME/.claude/browser-agent-configs/all-agents-mcp.json"
if [ -n "$PLATFORM" ]; then
    case "$PLATFORM" in
        reddit)   DM_MCP_CONFIG="$HOME/.claude/browser-agent-configs/reddit-agent-mcp.json" ;;
        linkedin) DM_MCP_CONFIG="$HOME/.claude/browser-agent-configs/linkedin-agent-mcp.json" ;;
        twitter|x) DM_MCP_CONFIG="$HOME/.claude/browser-agent-configs/twitter-agent-mcp.json" ;;
    esac
fi

gtimeout 5400 claude --strict-mcp-config --mcp-config "$DM_MCP_CONFIG" -p "$(cat "$PHASE_A_PROMPT")" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: DM reply claude exited with code $?"
rm -f "$PHASE_A_PROMPT"

# ═══════════════════════════════════════════════════════
# Cleanup
# ═══════════════════════════════════════════════════════
DM_SUMMARY=$(psql "$DATABASE_URL" -t -A -c "
    SELECT json_build_object(
        'total_convos', (SELECT COUNT(*) FROM dms WHERE conversation_status='active'),
        'total_messages', (SELECT COUNT(*) FROM dm_messages),
        'inbound', (SELECT COUNT(*) FROM dm_messages WHERE direction='inbound'),
        'outbound', (SELECT COUNT(*) FROM dm_messages WHERE direction='outbound'),
        'tier1', (SELECT COUNT(*) FROM dms WHERE tier=1 AND conversation_status='active'),
        'tier2', (SELECT COUNT(*) FROM dms WHERE tier=2 AND conversation_status='active'),
        'tier3', (SELECT COUNT(*) FROM dms WHERE tier=3 AND conversation_status='active'),
        'stale', (SELECT COUNT(*) FROM dms WHERE conversation_status='stale')
    );" 2>/dev/null || echo "{}")

log "DM pipeline summary: $DM_SUMMARY"

# Log run to persistent monitor per platform
RUN_ELAPSED=$(( $(date +%s) - RUN_START ))
DM_OUTBOUND=$(echo "$DM_SUMMARY" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('outbound',0))" 2>/dev/null || echo 0)
DM_STALE_CT=$(echo "$DM_SUMMARY" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('stale',0))" 2>/dev/null || echo 0)
if [ -z "$PLATFORM" ] || [ "$PLATFORM" = "reddit" ]; then
    python3 "$REPO_DIR/scripts/log_run.py" --script "dm_replies_reddit" --posted "$DM_OUTBOUND" --skipped "$DM_STALE_CT" --failed 0 --cost 0 --elapsed "$RUN_ELAPSED"
fi
if [ -z "$PLATFORM" ] || [ "$PLATFORM" = "linkedin" ]; then
    python3 "$REPO_DIR/scripts/log_run.py" --script "dm_replies_linkedin" --posted 0 --skipped 0 --failed 0 --cost 0 --elapsed 0
fi
if [ -z "$PLATFORM" ] || [ "$PLATFORM" = "twitter" ] || [ "$PLATFORM" = "x" ]; then
    python3 "$REPO_DIR/scripts/log_run.py" --script "dm_replies_twitter" --posted 0 --skipped 0 --failed 0 --cost 0 --elapsed 0
fi

# Report flagged conversations needing human attention (emails already sent per-DM during flagging)
FLAGGED_COUNT=$(psql "$DATABASE_URL" -t -A -c "
    SELECT COUNT(*) FROM dms WHERE conversation_status = 'needs_human';" 2>/dev/null || echo "0")

if [ "$FLAGGED_COUNT" -gt 0 ] 2>/dev/null; then
    log "ACTION REQUIRED: $FLAGGED_COUNT conversations flagged for human attention (escalation emails already sent per-DM)"
    log "Run: python3 ~/social-autoposter/scripts/dm_conversation.py show-flagged"

    platform_notify "Social DM Escalation" "$FLAGGED_COUNT DM conversations need your attention"
fi

# Delete old logs
find "$LOG_DIR" -name "engage-dm-replies-*.log" -mtime +7 -delete 2>/dev/null || true

log "=== DM reply engagement complete: $(date) ==="
