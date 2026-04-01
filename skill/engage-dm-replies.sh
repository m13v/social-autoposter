#!/usr/bin/env bash
# engage-dm-replies.sh — DM conversation reply loop
# Scans Reddit Chat, LinkedIn Messages, and X/Twitter DMs for new inbound messages,
# then replies to continue the conversation.
# Called by launchd every 4 hours.

set -euo pipefail

# DM lock: wait up to 60min for previous DM run to finish, then skip
source "$(dirname "$0")/lock.sh"
acquire_lock "dm-replies" 3600

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
LOG_FILE="$LOG_DIR/engage-dm-replies-$(date +%Y-%m-%d_%H%M%S).log"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG_FILE"; }

log "=== DM Reply Engagement Run: $(date) ==="

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
# Find conversations needing replies across all platforms
# ═══════════════════════════════════════════════════════

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

CRITICAL - Browser agent rules:
- Reddit Chat: use mcp__reddit-agent__* tools ONLY
- LinkedIn Messages: use mcp__linkedin-agent__* tools ONLY
- X/Twitter DMs: use mcp__twitter-agent__* tools ONLY
NEVER use generic mcp__playwright-extension__*, mcp__isolated-browser__*, or mcp__macos-use__* tools.
If a tool call is blocked or times out, wait 30 seconds and retry (up to 3 times). Do NOT fall back to other tools.

Our projects (for context when conversations touch relevant topics):
$PROJECTS

## PHASE A: Scan Reddit Chat for new messages

1. Navigate to https://www.reddit.com/chat using the reddit-agent browser
2. Wait for chat sidebar to load (3 seconds)
3. Look for chat rooms with unread indicators (bold text, notification badges)
4. For each chat room with unread messages:
   a. Click into the chat room
   b. Read the latest messages
   c. Identify the other person's username
   d. Log any new inbound messages to the database:
      \`\`\`bash
      cd ~/social-autoposter && python3 scripts/dm_conversation.py log-inbound --author "USERNAME" --content "MESSAGE_TEXT"
      \`\`\`
   e. If no existing DM record exists for this user, the script will tell you. Create one:
      \`\`\`bash
      source ~/social-autoposter/.env
      psql "\$DATABASE_URL" -c "INSERT INTO dms (platform, their_author, status, conversation_status, tier, project_name) VALUES ('reddit', 'USERNAME', 'sent', 'active', 1, NULL) RETURNING id;"
      \`\`\`
      Then set the chat URL:
      \`\`\`bash
      python3 scripts/dm_conversation.py set-url --author "USERNAME" --url "CHAT_URL"
      \`\`\`

## PHASE B: Scan LinkedIn Messages for new messages

1. Navigate to https://www.linkedin.com/messaging/ using the linkedin-agent browser
2. Look for conversations with unread indicators
3. For each unread conversation:
   a. Click into it, read the latest messages
   b. Identify the sender
   c. Check if this person is in our DM database:
      \`\`\`bash
      cd ~/social-autoposter && python3 scripts/dm_conversation.py find --author "PERSON_NAME"
      \`\`\`
   d. Log inbound messages the same way as Reddit

## PHASE C: Scan X/Twitter DMs for new messages

1. Navigate to https://x.com/messages using the twitter-agent browser
2. **ENCRYPTED DM PASSCODE**: Twitter may show an "Enter your passcode" or "encrypted_dm_passcode_required" dialog before you can access DMs. If you see this dialog:
   a. Find the passcode input field in the snapshot
   b. Type the passcode: $TWITTER_DM_PASSCODE
   c. Click "Confirm" or press Enter
   d. Wait for the DM inbox to load
   The passcode is loaded from .env as TWITTER_DM_PASSCODE.
3. Look for conversations with unread indicators
4. Same process as above - log inbound messages

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
4. **NEVER recommend a product in the first 4 exchanges.** Count the total messages. If there are fewer than 8 messages total (4 each), stay in rapport-building mode. No links, no product names.
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
- There are 8+ total messages in the conversation (at least 4 exchanges)
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

**Reddit Chat** (mcp__reddit-agent__* tools):
1. Navigate to the chat room (use chat_url if available, or find via sidebar)
2. Type the reply in the message input
3. Press Enter to send

**LinkedIn Messages** (mcp__linkedin-agent__* tools):
1. Navigate to the conversation
2. Type and send

**X/Twitter DMs** (mcp__twitter-agent__* tools):
1. Navigate to the conversation (if the encrypted DM passcode dialog appears, enter: $TWITTER_DM_PASSCODE and confirm)
2. Type and send

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
- How many new inbound messages found per platform
- How many replies sent
- How many flagged for human attention (list each with reason)
- How many left alone (no reply needed)
- How many marked stale
PROMPT_EOF

gtimeout 5400 claude -p "$(cat "$PHASE_A_PROMPT")" 2>&1 | tee -a "$LOG_FILE" || log "WARNING: DM reply claude exited with code $?"
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

# Report flagged conversations needing human attention
FLAGGED=$(psql "$DATABASE_URL" -t -A -c "
    SELECT json_agg(json_build_object(
        'dm_id', d.id, 'platform', d.platform, 'author', d.their_author,
        'reason', d.human_reason, 'chat_url', d.chat_url,
        'last_msg', (SELECT LEFT(content, 150) FROM dm_messages WHERE dm_id = d.id ORDER BY message_at DESC LIMIT 1)
    ))
    FROM dms d WHERE d.conversation_status = 'needs_human'
    ORDER BY d.flagged_at DESC;" 2>/dev/null || echo "null")

if [ "$FLAGGED" != "null" ] && [ -n "$FLAGGED" ]; then
    FLAGGED_COUNT=$(echo "$FLAGGED" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
    log "ACTION REQUIRED: $FLAGGED_COUNT conversations flagged for human attention"
    log "Run: python3 ~/social-autoposter/scripts/dm_conversation.py show-flagged"

    # macOS notification
    osascript -e "display notification \"$FLAGGED_COUNT DM conversations need your attention\" with title \"Social DM Escalation\" sound name \"Glass\"" 2>/dev/null || true

    # Build email body
    FLAGGED_BODY=$(echo "$FLAGGED" | python3 -c "
import json, sys
items = json.load(sys.stdin)
lines = []
for i in items:
    lines.append(f\"DM #{i['dm_id']} [{i['platform']}] {i['author']}\")
    lines.append(f\"  Reason: {i['reason']}\")
    if i.get('chat_url'): lines.append(f\"  URL: {i['chat_url']}\")
    if i.get('last_msg'): lines.append(f\"  Last: {i['last_msg'][:120]}\")
    lines.append('')
print('\n'.join(lines))
" 2>/dev/null || echo "Check flagged DMs")

    # Send email notification via Resend
    if [ -n "${RESEND_API_KEY:-}" ]; then
        curl -s -X POST 'https://api.resend.com/emails' \
            -H "Authorization: Bearer $RESEND_API_KEY" \
            -H 'Content-Type: application/json' \
            -d "$(python3 -c "
import json
body = '''$FLAGGED_BODY'''
print(json.dumps({
    'from': 'DM Pipeline <matt@fazm.ai>',
    'to': ['i@m13v.com'],
    'subject': f'DM Escalation: $FLAGGED_COUNT conversations need you',
    'text': 'The following DM conversations have been flagged for your personal attention:\n\n' + body + '\nReview: python3 ~/social-autoposter/scripts/dm_conversation.py show-flagged'
}))
" 2>/dev/null)" > /dev/null 2>&1 || log "WARNING: Failed to send escalation email"
    fi
fi

# Delete old logs
find "$LOG_DIR" -name "engage-dm-replies-*.log" -mtime +7 -delete 2>/dev/null || true

log "=== DM reply engagement complete: $(date) ==="
